"""Explicitly opted-in real-provider Lead conversation evidence runner.

Normal cases delegate every provider result unchanged. Named fault cases retain
both the actual native result and the injected delta in a private directory.
This module is inert on import and never installs or authenticates a provider.
"""
from __future__ import annotations
import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
from unittest import mock
from story_writing_agents import AgentBackend, BackendProtocolError, StoryRuntime
from story_writing_agents import antigravity_adapter, codex_adapter
from story_writing_agents.agent_context import canonical_provider_json
from story_writing_agents.agents import ROSTER
from story_writing_agents.conversations import CollaborationConfirmationDecision, CollaborationExecutionResult, CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload, CollaborationSpecialistReplyPayload, CollaborationProviderCallPayload, CollaborationStepState, LeadOutcomeKind, LeadStepDecision, project_collaboration_history
from story_writing_agents.provider_process import ProcessResult
from story_writing_agents.conversations import encode_collaboration_event, CollaborationProposal
from story_writing_agents.specialist_routing import (AuthoringActivity, SpecialistAssignmentMode,
    required_authoring_specialists, validate_specialist_assignment)
from story_writing_agents.workspace import initialize_workspace
MODEL_BY_CASE = {'L01-PRO-HIGH-PTY': 'gemini-3.1-pro-high', 'L02-PRO-LOW-ONE': 'gemini-3.1-pro-low', 'L03-FLASH-THREE': 'gemini-3.8-flash-low', 'L04-SONNET-ONE': 'claude-sonnet-4-6', 'L05-MIXED': 'gemini-3.1-pro-high', 'L09-SECOND-ROUND': 'gemini-3.1-pro-high', 'F05-DIAGNOSTICS-RECOVERY': 'gemini-3.1-pro-high'}
AGENTS_BY_CASE = {'L01-PRO-HIGH-PTY': ('plot_architect', 'character_custodian'), 'L02-PRO-LOW-ONE': ('plot_architect',), 'L03-FLASH-THREE': ('plot_architect', 'character_custodian', 'execution_specialist'), 'L04-SONNET-ONE': ('plot_architect',), 'L05-MIXED': ('plot_architect', 'character_custodian')}
OUTLINE = 'Invitation occurs on day 1.\nThe sealed door is revealed on day 3.\nThe narrator records observations without passing judgment.\n'
CHARACTERS = 'Nara is the observer.\nNo motive for opening the sealed door has been supplied.\n'
DRAFT = 'Nara receives the invitation on day 1.\nThe sealed door is revealed on day 1.\nThe narrator repeats the same report twice.\nThe narrator repeats the same report twice.\n'
CONSISTENT_CANDIDATE = ('Nara receives the invitation on day 1.\n'
                        'Nara observes the sealed door being revealed on day 3.\n'
                        'Nara records these observations without judging anyone.\n')
MARKER = 'FLOW623-RECALL'


WORKFLOW_FOLLOWUP = ('Treat this as a fresh workflow-only status task, not continued story authoring. '
                    'Repeat the conversation marker, then report only which consultation completed '
                    'and its recorded state. Do not interpret, critique, revise or recommend story content.')


def consultation_request(spec, agents, *, text=None, references=True):
    request = text or spec['instruction']
    if spec.get('candidate_text') and not (text is not None and text == spec.get('recovery_instruction')):
        request += '\nFinished user-supplied candidate (review this exact text):\n' + spec['candidate_text']
    request += (' Disclose REVIEW mode, the concrete candidate input aliases and bounded review criteria '
                'for each assignment. Use a REVIEW authoring scope with exactly the requested domains. '
                'Preserve the current task/revision for a continuation; use a fresh task for independent work. '
                'Produce a consultation proposal for exactly these reviewers in this order: '
                + ', '.join(agents) + '. Do not execute before structured Confirm. Do not propose artifact changes. ')
    if references:
        if 'plot_architect' in agents:
            request += ' ${core_documents/global_outline.md}'
        if 'character_custodian' in agents:
            request += ' ${core_documents/character_sheets.md}'
        if 'execution_specialist' in agents:
            request += ' ${manuscript/part-01/opening.md}'
    return request


def assert_review_proposal(proposal, agents):
    scope = proposal.authoring_scope
    require(scope is not None and scope.activity is AuthoringActivity.REVIEW,
            'Live review proposal lacks REVIEW task scope')
    paths = []
    for item in proposal.basis.inputs:
        if item.kind.value in {'core_document', 'manuscript', 'writing_artifact'}:
            locator = item.locator
            paths.append(locator[2:locator.index('}')] if locator.startswith('${') else locator)
    require(required_authoring_specialists(scope, affected_paths=tuple(paths)) == tuple(agents)
            and proposal.participants == tuple(agents), 'Review scope/reference domains differ from exact roster')
    inputs = {item.input_id: item for item in proposal.basis.inputs}
    for assignment in proposal.assignments:
        require(assignment.mode is SpecialistAssignmentMode.REVIEW and assignment.review_basis is not None,
                'Live review assignment lacks mode or concrete review basis')
        validate_specialist_assignment(assignment.mode, assignment.input_ids, assignment.review_basis)
        for alias in assignment.review_basis.candidate_input_ids:
            require(alias in inputs and inputs[alias].state.value == 'present'
                    and inputs[alias].kind.value in {'user_input', 'core_document', 'manuscript',
                                                    'writing_artifact', 'specialist_reply'},
                    'Review candidate is unavailable or is not candidate material')
    return proposal


def assert_scoped_synthesis(proposal, replies, synthesis):
    require(proposal.authoring_scope is not None
            and synthesis.authoring_scope == proposal.authoring_scope,
            'Synthesis task scope differs from actual proposal')
    require((synthesis.proposal_id, synthesis.proposal_revision, synthesis.proposal_digest)
            == (proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest),
            'Synthesis proposal correlation differs')
    require(synthesis.reply_event_ids == tuple(e.event_id for e in replies),
            'Synthesis reply attribution differs')
    assignments = {a.assignment_id: a for a in proposal.assignments}
    require(len(replies) == len(assignments)
            and {e.payload.assignment_id for e in replies} == set(assignments)
            and len({e.event_id for e in replies}) == len(replies),
            'Scoped synthesis lacks complete assignments')
    for event in replies:
        reply = event.payload
        assignment = assignments.get(reply.assignment_id)
        require(assignment is not None and event.sender.agent_id == assignment.specialist_id
                and reply.input_ids == assignment.input_ids and reply.mode is assignment.mode,
                'Reply mode or assignment correlation differs')
        require((reply.proposal_id, reply.proposal_revision, reply.proposal_digest, reply.execution_attempt_id)
                == (synthesis.proposal_id, synthesis.proposal_revision, synthesis.proposal_digest,
                    synthesis.execution_attempt_id), 'Reply proposal/attempt correlation differs')
        validate_specialist_assignment(assignment.mode, assignment.input_ids, assignment.review_basis)
        assessment = reply.assessment.value
        if assignment.mode is SpecialistAssignmentMode.DRAFT:
            require(assessment in {'drafted', 'insufficient_evidence'}, 'Draft reply claims review assessment')
        else:
            require(assignment.mode is SpecialistAssignmentMode.REVIEW
                    and assessment in {'supports', 'concern', 'conflict', 'insufficient_evidence'},
                    'Review reply claims drafting success')
        if assessment in {'concern', 'conflict', 'insufficient_evidence'}:
            require(set(reply.input_ids) <= set(synthesis.unresolved_input_ids),
                    'Synthesis erased unresolved inputs')


def assert_workflow_judgment(history, result):
    require(result.kind in {LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE},
            'Workflow-only outcomes cannot disclose recommendation authority')
    judgments = [e.payload for e in history if isinstance(e.payload, CollaborationLeadJudgmentPayload)]
    require(judgments and judgments[-1].kind is result.kind
            and judgments[-1].authoring_scope is not None
            and judgments[-1].authoring_scope.activity is AuthoringActivity.WORKFLOW
            and not judgments[-1].authoring_scope.domains and not judgments[-1].authoring_scope.target_paths,
            'Direct outcome lacks fresh workflow-only scope')
    prior_story = [e.payload.authoring_scope for e in history
                   if getattr(e.payload, 'authoring_scope', None) is not None
                   and e.payload.authoring_scope.activity is not AuthoringActivity.WORKFLOW]
    require(not prior_story or judgments[-1].authoring_scope.task_id != prior_story[-1].task_id,
            'Workflow followup reused a story task')

def assert_pending_plot_recommendation(history, result, calls):
    """Accept one scoped consultation preparation request, before any decision."""
    from story_writing_agents import conversations as contracts
    require(result.kind is LeadOutcomeKind.RECOMMENDATION
            and result.next_action is contracts.CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION,
            f'L06C lacks separately confirmable recommendation: {result!r}')
    request = result.recommendation_request
    require(type(request) is contracts.LeadRecommendationRequestPayload,
            'L06C lacks an exact typed recommendation request')
    scope = request.authoring_scope
    require(scope is not None and scope.activity is AuthoringActivity.REVIEW,
            'L06C requires substantive REVIEW scope')
    require(required_authoring_specialists(scope) == ('plot_architect',)
            and request.specialist_ids == ('plot_architect',)
            and request.kind is contracts.LeadRecommendationActionKind.PREPARE_CONSULTATION,
            'L06C must disclose exactly the required Plot consultation')
    require(request.objective_id is None and request.target_paths == (),
            'L06C consultation preparation acquired artifact authority')
    require(all(getattr(result, name) is None for name in
                ('state', 'proposal', 'confirmation_request_id', 'artifact_proposal',
                 'artifact_application_request', 'story_commit_request')),
            'L06C created downstream proposal or write authority')
    requests = [e for e in history if isinstance(e.payload, contracts.LeadRecommendationRequestPayload)]
    require(len(requests) == 1 and requests[0].payload == request
            and requests[0].event_id == request.request_event_id
            and history[-1] == requests[0] and history[-1].sequence == request.tail_sequence
            and all(getattr(requests[0], name) == getattr(result, name) for name in
                    ('session_id', 'conversation_id', 'turn_id', 'step_id'))
            and result.evidence == request.basis.evidence,
            'L06C request differs from the exact durable tail')
    source = [e for e in history if e.event_id == request.source_recommendation_event_id]
    require(len(source) == 1 and isinstance(source[0].payload, CollaborationLeadJudgmentPayload),
            'L06C source judgment is absent')
    judgment = source[0].payload
    require(judgment.kind is LeadOutcomeKind.RECOMMENDATION and judgment.authoring_scope == scope,
            'L06C source/request task scope differs')
    # The durable request owns the action; the judgment retains source facts.
    require(dataclasses.replace(request) == request,
            'L06C request action fields or digest differ')
    require(request.source_recommendation_digest == 'sha256:' + digest(judgment.message.encode('utf-8')),
            'L06C source message digest differs')
    forbidden = (contracts.CollaborationAssignmentDispatchPayload, contracts.CollaborationConfirmationPayload,
                 contracts.LeadRecommendationDecisionPayload, contracts.LeadRecommendationStartedPayload,
                 contracts.LeadRecommendationResultPayload, contracts.ArtifactApplicationRequestPayload,
                 contracts.ArtifactApplicationDecisionPayload, contracts.ArtifactApplicationResultPayload,
                 contracts.StoryCommitRequestPayload, contracts.StoryCommitDecisionPayload,
                 contracts.StoryCommitResultPayload)
    require(not any(isinstance(e.payload, forbidden) for e in history)
            and all(call['agent'] == 'lead_orchestrator' for call in calls),
            'L06C executed or fabricated unconfirmed downstream authority')
    projection = project_collaboration_history(history)
    require(projection.current_recommendation_request == request
            and projection.current_recommendation_state is contracts.LeadRecommendationState.PENDING
            and projection.current_recommendation_decision is None
            and projection.current_recommendation_start is None
            and projection.current_recommendation_result is None
            and not projection.artifact_objectives,
            'L06C durable recommendation is not exclusively pending')
    return request


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

def source_closure():
    """Pin source/configuration and this runner without exposing local paths."""
    root = Path(__file__).resolve().parents[1]
    paths = list((root / 'src/story_writing_agents').rglob('*.py'))
    paths += [p for p in (root / 'config').rglob('*') if p.is_file()]
    paths += [Path(__file__).resolve(), root / 'tests/test_antigravity_live_conversations.py',
              root / 'tests/fixtures/antigravity_live_cases.json']
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(paths)}


def decision(turn, kind=CollaborationConfirmationDecision.CONFIRM, **changes):
    require(turn.proposal is not None, 'Expected an actual pending proposal')
    p = turn.proposal
    values = dict(confirmation_request_id=turn.confirmation_request_id, session_id=turn.session_id, conversation_id=turn.conversation_id, step_id=turn.step_id, proposal_id=p.proposal_id, proposal_revision=p.proposal_revision, proposal_digest=p.proposal_digest, kind=kind, revision_request=None)
    values.update(changes)
    return LeadStepDecision(**values)

def git_snapshot(root):
    def output(*arguments):
        return subprocess.check_output(['git', *arguments], cwd=root)
    return {'head': output('rev-parse', 'HEAD').decode().strip(),
            'index_sha256': digest(output('ls-files', '--stage', '-z'))}


def semantic_digest(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode())


def stage_seconds(maximum_calls, *, overhead=180):
    """Allow each runtime-bounded call its native timeout without a retry."""
    require(type(maximum_calls) is int and maximum_calls >= 0, 'Invalid stage call bound')
    return maximum_calls * load_inventory()['budget']['per_call_seconds'] + overhead


def call_input(call):
    directory = Path(call['directory'])
    command = json.loads((directory / 'command.private.json').read_text())['argv']
    payload = (command[command.index('--print') + 1].encode()
               if call['vendor'] == 'antigravity' else (directory / 'stdin.bin').read_bytes())
    require(digest(payload) == call['payload_sha256'], 'Recorded payload bytes differ')
    return json.loads(payload), command


def canonical_history(history):
    """Preserve public event discriminators for typed runtime history."""
    return [event if isinstance(event, dict)
            else json.loads(encode_collaboration_event(event)) for event in history]


def prove_lead_corrections(entries, history):
    """Prove only the observed, bounded runtime Lead validation correction."""
    events = canonical_history(history)
    proofs = {}
    for rejected in entries:
        if rejected['outcome'] != 'rejected':
            continue
        call = rejected['call']
        require(rejected['code'] == 'answer_validation_failed' and call['vendor'] == 'antigravity' and (call['agent'] == 'lead_orchestrator') and (call['fault'] is None) and (call['delivered_returncode'] == 0) and (rejected['terminal']['status'] == 'SUCCESS'), 'Unapproved rejected provider response')
        payload = rejected['payload']
        rejected_output = rejected['terminal'].get('structured_output', {})
        require(set(rejected_output) == {'agent_action'} and rejected_output.get('agent_action', {}).get('action_type') == 'submit_result', 'Rejected native answer response shape or action differs')
        current = payload.get('task_input', {})
        identity = current.get('identity')
        require(type(identity) is dict and current.get('workflow_type') == 'lead_user_turn' and ('response_correction' not in current), 'Rejected response is not the first Lead attempt')
        candidates = [entry for entry in entries if entry['call']['number'] > call['number'] and entry['payload'].get('request_correlation_token') == payload.get('request_correlation_token') and (entry['payload'].get('task_input', {}).get('identity') == identity) and ('response_correction' in entry['payload'].get('task_input', {}))]
        require(len(candidates) == 1, 'Missing or multiple Lead corrections')
        corrected = candidates[0]
        corrected_input = dict(corrected['payload']['task_input'])
        marker = corrected_input.pop('response_correction')
        require(type(marker) is dict and marker.get('rejection_code') == 'answer_validation_failed' and (type(marker.get('instruction')) is str) and bool(marker['instruction']) and (corrected_input == current), 'Correction marker or original request differs')
        q = corrected['payload'].get('response_contract', [])
        require(q.get('workflow_phase') == 'lead_user_turn' and q.get('allowed_action_types') == ['submit_result'] and q.get('searchable_source_types') == [], 'Correction did not disable retrieval')
        require(corrected['outcome'] == 'accepted' and corrected['code'] == 'accepted' and (corrected['call']['fault'] is None) and (corrected['terminal']['status'] == 'SUCCESS') and (corrected['call']['delivered_returncode'] == 0), 'Corrective Lead answer was not accepted')
        between = [entry for entry in entries if call['number'] < entry['call']['number'] < corrected['call']['number']]
        references = bool(payload.get('untrusted_attached_references'))
        require(len(between) == int(references), 'Correction exceeds exact prelude/answer bound')
        operation_id = None
        if references:
            prelude = between[0]
            prelude_call = prelude['call']
            correlation = prelude['payload'].get('correlation', {})
            operation_id = correlation.get('operation_id')
            require(prelude['payload'].get('request_type') == 'writing_artifact_reference_read' and correlation.get('agent_id') == 'lead_orchestrator' and (correlation.get('phase') == 'lead') and (prelude_call['prior_thread_id'] is None) and (prelude['outcome'] == 'accepted'), 'Corrective reference prelude differs')
            fresh_thread = prelude_call['thread_id']
            require(corrected['call']['prior_thread_id'] == fresh_thread, 'Correction did not continue its fresh prelude thread')
        else:
            require(corrected['call']['prior_thread_id'] is None, 'Correction reused the rejected thread')
            fresh_thread = corrected['call']['thread_id']
        require(fresh_thread != call['thread_id'] and corrected['call']['thread_id'] == fresh_thread, 'Correction native thread is not fresh or consistent')
        logical_calls = [entry for entry in entries if entry['call']['agent'] == 'lead_orchestrator' and (entry['payload'].get('request_correlation_token') == payload.get('request_correlation_token') or (operation_id is not None and entry['payload'].get('correlation', {}).get('operation_id') == operation_id))]
        require(logical_calls[-1] is corrected and len(logical_calls) <= 6 + int(references), 'Logical Lead invocation exceeds its source-derived bound')
        matching = [event for event in events if all((event.get(key) == identity[key] for key in ('session_id', 'conversation_id', 'turn_id', 'step_id')))]
        submitted = [event for event in matching if event['payload'].get('payload_type') == 'provider_call' and event['payload'].get('role') == 'lead_orchestrator' and (event['payload'].get('invocation_kind') == 'lead') and (event['payload'].get('state') == 'submitted')]
        require(len(submitted) == len(logical_calls) and submitted, 'Durable correction physical-call count differs')
        invocations = {event['payload']['invocation_id'] for event in submitted}
        require(len(invocations) == 1 and [event['payload']['ordinal'] for event in submitted] == list(range(1, len(submitted) + 1)), 'Wrong durable invocation or ordinal')
        call_ids = [event['payload']['call_id'] for event in submitted]
        require(len(set(call_ids)) == len(call_ids), 'Duplicate durable submission')
        for entry, event in zip(logical_calls, submitted):
            is_prelude = entry['payload'].get('request_type') == 'writing_artifact_reference_read'
            require((event['payload']['call_kind'] == 'reference_prelude') == is_prelude, 'Native action and durable physical-call ordinal differ')
            prepared = [item for item in matching if item['payload'].get('payload_type') == 'provider_call' and item['payload'].get('state') == 'prepared' and (item['payload'].get('call_id') == event['payload']['call_id'])]
            require(len(prepared) == 1 and prepared[0]['sequence'] < event['sequence'] and all((prepared[0]['payload'][key] == event['payload'][key] for key in ('invocation_id', 'ordinal', 'role', 'invocation_kind'))), 'Durable prepared/submitted correction facts differ')
        output = corrected['terminal']['structured_output']
        require(type(output) is dict and set(output) == {'agent_action'}, 'Corrected native response shape differs')
        answer = output['agent_action']
        require(set(answer) == {'action_type', 'task_result'} and answer['action_type'] == 'submit_result', 'Correction is not terminal answer')
        value = answer['task_result']
        judgments = [event for event in matching if event['payload'].get('payload_type') == 'lead_judgment']
        require(len(judgments) == 1 and judgments[0]['sequence'] > submitted[-1]['sequence'] and judgments[0]['payload']['kind'] == {'clarification_question': 'question', 'proposal_drafting_recommendation': 'recommendation'}.get(value['response_type'], value['response_type']) and judgments[0]['payload']['message'] == value['response_text'], 'Final accepted durable Lead judgment differs')
        proofs[call['number']] = {'rejected_call': call['number'], 'corrected_call': corrected['call']['number'], 'fresh_thread': fresh_thread, 'fresh_start_call': between[0]['call']['number'] if references else corrected['call']['number'], 'logical_invocation_id': next(iter(invocations)), 'physical_calls': len(logical_calls)}
    return proofs


BUDGET_REVIEWERS = ('plot_architect', 'character_custodian', 'execution_specialist')


def budget_synthesis_material(payload):
    """Recognize only the current named consultation summary request."""
    if type(payload) is not dict:
        return None
    from tests.test_agent_context_accounting import _expanded_context_payload
    material = _expanded_context_payload(payload).get('task_input')
    return material if type(material) is dict and material.get('workflow_type') == 'consultation_summary' else None

def inject_budget_next_intent(stdout, payload, *, agent, returncode, physical_calls_spent):
    """Inject one schema-valid continuation into a real completed summary."""
    material = budget_synthesis_material(payload)
    require(material is not None and agent == 'lead_orchestrator' and returncode == 0,
            'Budget fault requires the current successful Lead synthesis')
    require(type(physical_calls_spent) is int and 4 <= physical_calls_spent <= 12,
            'Budget fault requires the actual durable submitted-call count')
    native = json.loads(stdout)
    require(native.get('status') == 'SUCCESS', 'Budget synthesis must complete with SUCCESS')
    output = native.get('structured_output')
    require(type(output) is dict and set(output) == {'agent_action'}, 'Budget synthesis has an invalid outer object')
    action = output['agent_action']
    require(type(action) is dict and set(action) == {'action_type', 'task_result'} and action['action_type'] == 'submit_result', 'Budget synthesis must submit its task result')
    result = action['task_result']
    require(type(result) is dict and set(result) in ({'response_type', 'response_text'}, {'response_type', 'response_text', 'follow_up_consultation'}) and result['response_type'] == 'consultation_summary', 'Budget synthesis fields differ')
    require(type(result['response_text']) is str and bool(result['response_text'].strip()), 'Budget synthesis message is absent')
    inputs = material['inputs']
    input_ids = [item['input_source_id'] for item in inputs]
    reply_ids = [item['reply_event_id'] for item in material['replies']]
    require(len(reply_ids) == 3 and len(set(reply_ids)) == 3, 'Budget synthesis requires three actual replies')
    references = [item for item in inputs if item['input_source_type'] in {'core_document', 'manuscript', 'story_document'}]
    require(references, 'Budget synthesis has no genuine reference prelude basis')
    minimum = 2 * len(BUDGET_REVIEWERS) + 2
    require(minimum > 12 - physical_calls_spent, 'Requested continuation fits the remaining budget')
    require(material['provider_constraints']['current_writing_task_scope']['activity_type'] == 'review_writing', 'Budget continuation requires the current review task')
    intent = {'consultation_purpose': 'Reconsider the unresolved finding.', 'assignments': [
        {'agent_id': reviewer, 'task_instructions': 'Review the original inputs and all replies again.',
         'input_source_ids': [*input_ids, *reply_ids], 'assignment_type': 'review_content'}
        for reviewer in BUDGET_REVIEWERS]}
    before = result.get('follow_up_consultation')
    result['follow_up_consultation'] = intent
    delivered = stdout if before == intent else canonical_provider_json(native)
    proof = {'fault': 'budget_next_intent', 'path': 'structured_output.agent_action.task_result.follow_up_consultation',
             'physical_calls_spent': physical_calls_spent, 'minimum_required_calls': minimum,
             'remaining_calls': 12 - physical_calls_spent, 'changed': before != intent,
             'original_stdout_sha256': digest(stdout), 'delivered_stdout_sha256': digest(delivered)}
    return delivered, proof

def audit_exchanges(exchanges, calls, history=()):
    """Correlate actual transport, durable capture and exact diagnostic bytes."""
    import base64
    groups = {}
    for record in exchanges:
        groups.setdefault(record['exchange_id'], []).append(record)
    remaining = list(groups.values())
    audit = []
    entries = []
    for call in calls:
        matches = []
        for records in remaining:
            request = records[0]
            if request['request_type'] != 'request' or request['agent_id'] != call['agent']:
                continue
            content = json.loads(request['content'])
            if semantic_digest(content['payload']) == call['payload_semantic_sha256']:
                matches.append(records)
        if len(matches) > 1 and 'delivered_returncode' in call:
            directory = Path(call['directory'])
            stdout = (directory / 'delivered.stdout').read_bytes()
            stderr = (directory / 'delivered.stderr').read_bytes()
            identified = []
            for candidate in matches:
                responses = [json.loads(record['content']) for record in candidate
                             if record['request_type'] == 'response']
                if len(responses) != 1:
                    continue
                response = responses[0]
                diagnostic = response.get('response_diagnostic')
                try:
                    if diagnostic is not None:
                        def decoded_stream(item):
                            if item['encoding'] == 'utf-8':
                                return item['data'].encode()
                            if item['encoding'] == 'base64':
                                return base64.b64decode(item['data'], validate=True)
                            raise ValueError('Unknown diagnostic stream encoding')

                        status = None
                        error = None
                        if stdout:
                            try:
                                parsed = json.loads(stdout.decode('utf-8', 'replace'))
                                if isinstance(parsed, dict):
                                    target = parsed.get('result') if isinstance(parsed.get('result'), dict) else parsed
                                    if 'error' in target:
                                        error = target['error']
                                    elif 'error' in parsed:
                                        error = parsed['error']
                                    if 'status' in target:
                                        status = target['status']
                                    elif 'status' in parsed:
                                        status = parsed['status']
                            except Exception:
                                pass

                        exact = (diagnostic['exit_code'] == call['delivered_returncode']
                                 and diagnostic.get('status') == status
                                 and diagnostic.get('error') == error
                                 and decoded_stream(diagnostic['stderr']) == stderr)
                    else:
                        native_value = (json.loads(stdout)['structured_output']
                                        if call['vendor'] == 'antigravity' else
                                        json.loads((directory / 'codex-response.raw').read_bytes()))
                        exact = (not response.get('provider_failure')
                                 and response.get('thread_id') == call.get('thread_id')
                                 and semantic_digest(json.loads(response['response_text']))
                                 == semantic_digest(native_value))
                except (KeyError, TypeError, ValueError):
                    exact = False
                if exact:
                    identified.append(candidate)
            matches = identified
        require(len(matches) == 1, 'Actual call has no unambiguous durable REQUEST payload')
        records = matches[0]
        remaining.remove(records)
        kinds = [r['request_type'] for r in records]
        require(kinds.count('request') == 1 and kinds.count('outcome') == 1
                and kinds[0] == 'request' and kinds[-1] == 'outcome',
                'Exchange request/outcome lifecycle differs')
        request = json.loads(records[0]['content'])
        require(semantic_digest(request['schema']) == call['schema_semantic_sha256'],
                'Captured schema differs from actual submitted schema')
        require(request['prior_thread_id'] == call['prior_thread_id'],
                'Submitted resume thread differs from durable request')
        responses = [json.loads(r['content']) for r in records if r['request_type'] == 'response']
        terminal_outcome = json.loads(records[-1]['content'])
        outcome = terminal_outcome['outcome']
        payload, command = call_input(call)
        require(semantic_digest(payload) == call['payload_semantic_sha256'],
                'Recorded payload semantic digest differs')
        completed = 'delivered_returncode' in call
        require(len(responses) == (1 if completed or outcome == 'provider_failure' else 0),
                'Completed/unavailable native output has incorrect RESPONSE count')
        row = dict(number=call['number'], payload_sha256=call['payload_sha256'],
                   outcome=outcome, completed_output=completed)
        if not completed:
            require(call.get('exception_type') is not None and outcome in {'cancelled', 'provider_failure'},
                    'Unavailable output was accepted or unexplained')
            if outcome == 'provider_failure':
                require(responses[0] == {'provider_failure': True}
                        and responses[0]['provider_failure'] is True,
                        'Unavailable failure response must be the exact synthetic marker')
        else:
            response = responses[0]
            directory = Path(call['directory'])
            native = (directory / 'delivered.stdout').read_bytes()
            stderr = (directory / 'delivered.stderr').read_bytes()
            if call['fault'] is None:
                require(native == (directory / 'stdout.raw').read_bytes()
                        and stderr == (directory / 'stderr.raw').read_bytes(),
                        'Unmodified native stream bytes differ')
            if call['fault'] == 'budget_next_intent':
                require(call['vendor'] == 'antigravity', 'Budget fault changed provider route')
                proof = json.loads((directory / 'injected-delta.sanitized.json').read_text())
                expected, actual_proof = inject_budget_next_intent(
                    (directory / 'stdout.raw').read_bytes(), payload,
                    agent=call['agent'], returncode=call['returncode'],
                    physical_calls_spent=proof['physical_calls_spent'])
                require(native == expected and proof == actual_proof
                        and stderr == (directory / 'stderr.raw').read_bytes()
                        and call['delivered_returncode'] == call['returncode'],
                        'Budget fault changed fields outside the one declared next_intent')
            terminal = json.loads(native) if call['vendor'] == 'antigravity' and call['fault'] not in {'utf8', 'json'} else None
            if terminal is not None:
                require(terminal.get('conversation_id') == call.get('thread_id'),
                        'Actual native thread differs from recorded transport')
            if terminal is not None and call['fault'] != 'schema':
                expected_schema = command[command.index('--json-schema') + 1].encode()
                require(canonical_provider_json(terminal.get('json_schema')) == expected_schema,
                        'Exact native schema echo differs')
            rejected = call['fault'] in {'nonzero', 'utf8', 'json', 'unrelated_error', 'schema'}
            require(outcome == 'provider_failure' if rejected else outcome in {'accepted', 'rejected'},
                    'Captured outcome differs from designated adapter result')
            require(bool(response.get('provider_failure')) == rejected,
                    'Failure response entered the accepted response shape')
            diagnostic = response.get('response_diagnostic')
            error_envelope = call['vendor'] == 'antigravity' and (
                call.get('native_status') == 'ERROR' or call['fault'] == 'known_error')
            if rejected or error_envelope:
                require(diagnostic is not None, 'Completed error bytes were not captured')
            if diagnostic is not None:
                require(diagnostic['exit_code'] == call['delivered_returncode'], 'Diagnostic exit differs')
                status = None
                error = None
                if native:
                    try:
                        parsed = json.loads(native.decode('utf-8', 'replace'))
                        if isinstance(parsed, dict):
                            target = parsed.get('result') if isinstance(parsed.get('result'), dict) else parsed
                            if 'error' in target:
                                error = target['error']
                            elif 'error' in parsed:
                                error = parsed['error']
                            if 'status' in target:
                                status = target['status']
                            elif 'status' in parsed:
                                status = parsed['status']
                    except Exception:
                        pass
                require(diagnostic.get('status') == status, 'Diagnostic status differs')
                require(diagnostic.get('error') == error, 'Diagnostic error differs')
                for name, expected in (('stderr', stderr),):
                    item = diagnostic[name]
                    try:
                        expected.decode('utf-8', 'strict')
                        encoding = 'utf-8'
                    except UnicodeDecodeError:
                        encoding = 'base64'
                    require(item['encoding'] == encoding, 'Diagnostic stream encoding differs')
                    decoded = (item['data'].encode() if encoding == 'utf-8'
                               else base64.b64decode(item['data'], validate=True))
                    require(decoded == expected, 'Diagnostic stream bytes differ')
            if not rejected:
                require(response['actual_transport_utf8_bytes'] == call['payload_bytes'],
                        'Exact per-call transport accounting differs')
                require(response['thread_id'] == call.get('thread_id'), 'Accepted native thread differs')
                actual_value = (json.loads(native)['structured_output'] if call['vendor'] == 'antigravity'
                                else json.loads((directory / 'codex-response.raw').read_bytes()))
                require(semantic_digest(json.loads(response['response_text'])) == semantic_digest(actual_value),
                        'Captured accepted result differs from actual native structured output')
        entries.append({'call': call, 'payload': payload, 'outcome': outcome,
                        'code': terminal_outcome['code'],
                        'terminal': terminal if completed else None})
        audit.append(row)
    proofs = prove_lead_corrections(entries, history)
    for row in audit:
        if row['outcome'] == 'rejected':
            require(row['number'] in proofs, 'Rejected response has no bounded correction proof')
            row['correction_proof'] = proofs[row['number']]
    require(not remaining, 'Durable request has no corresponding actual call')
    return audit


def assert_resumed_call_chain(calls, previous_thread, audit):
    """Allow a fresh thread only at an already proven correction boundary."""
    require(calls and calls[0]['prior_thread_id'] == previous_thread,
            'First resumed call lost the original native thread')
    handoffs = {row['correction_proof']['fresh_start_call']: row['correction_proof']['fresh_thread']
                for row in audit if 'correction_proof' in row}
    expected = previous_thread
    for call in calls:
        if call['prior_thread_id'] != expected:
            require(call['prior_thread_id'] is None
                    and handoffs.get(call['number']) == call.get('thread_id'),
                    'Unexplained fresh native thread after resume')
        expected = call['thread_id']


def audit_case_exchanges(case, calls=None):
    exchanges = [json.loads(line)
                 for path in case.workspace.path('debug_logs/agent_exchanges/live').glob('*.jsonl')
                 for line in path.read_text().splitlines()]
    return audit_exchanges(exchanges, case.observer.calls if calls is None else calls, case.history())


def normalized_visible(value):
    return ''.join(character for character in value
                   if not character.isspace() and character not in '│┃')


def assert_visible_messages(text, history):
    visible = normalized_visible(text)
    for event in history:
        if isinstance(event.payload, (CollaborationLeadJudgmentPayload,
                                      CollaborationSpecialistReplyPayload,
                                      CollaborationLeadSynthesisPayload)):
            require(normalized_visible(event.payload.message) in visible,
                    'Actual terminal text differs from a persisted visible message')


SEEDED_ARTIFACT_PATHS = frozenset({
    'core_documents/global_outline.md', 'core_documents/character_sheets.md',
    'manuscript/part-01/opening.md', 'manuscript/part-02/schedule.md',
})


def assert_seeded_artifacts(workspace, baseline):
    require(type(baseline) is dict and set(baseline) == SEEDED_ARTIFACT_PATHS,
            'Missing or incomplete original artifact baseline')
    for name, expected in baseline.items():
        require(type(expected) is str and len(expected) == 64
                and all(c in '0123456789abcdef' for c in expected),
                'Invalid original artifact digest')
        require(digest(workspace.path(name).read_bytes()) == expected,
                'Original fixture artifact changed')


def restore_artifact_baseline(directory, workspace, checkpoint):
    raw = (directory / 'initial-artifacts.private.json').read_bytes()
    require(digest(raw) == checkpoint['artifact_baseline_sha256'],
            'Original artifact baseline checkpoint differs')
    baseline = json.loads(raw)
    assert_seeded_artifacts(workspace, baseline)
    return baseline


def assert_character_recovery(history, failed_prefix, failed_call, recovery_calls,
                              proposed, completed):
    require(canonical_history(history[:len(failed_prefix)]) == failed_prefix,
            'Recovery rewrote failed execution history')
    require(completed.state is CollaborationStepState.COMPLETED,
            'Recovered consultation did not complete')
    require(completed.step_id == proposed.step_id, 'Recovery completed another step')
    require(tuple(a.specialist_id for a in proposed.proposal.assignments)
            == ('character_custodian',), 'Recovery roster differs from faulted agent')
    calls = [c for c in recovery_calls if c['agent'] == 'character_custodian']
    require(calls and all(c['agent'] in ('lead_orchestrator', 'character_custodian')
                         for c in recovery_calls), 'Faulted character recovery call missing')
    first = calls[0]
    require(failed_call['agent'] == 'character_custodian'
            and failed_call['fault'] == 'nonzero' and failed_call.get('thread_id'),
            'Faulted character identity missing')
    require(first['prior_thread_id'] is None and not first['resumed']
            and first.get('thread_id') and first['thread_id'] != failed_call['thread_id'],
            'Faulted character reused native thread')
    for call in calls:
        require(call['vendor'] == failed_call['vendor']
                and call['model'] == failed_call['model'] and call['fault'] is None,
                'Recovered character provider identity differs')
        # Adapter and final exchange audit own native acceptance, including recovered ERROR.
        require(call['delivered_returncode'] == 0,
                'Recovered character native call failed')
    old_stage = json.loads((Path(failed_call['directory']) / 'staged.sanitized.json').read_text())
    new_stage = json.loads((Path(first['directory']) / 'staged.sanitized.json').read_text())
    require(old_stage == new_stage, 'Recovered character staged definition changed')
    if len(calls) > 1:
        assert_resumed_call_chain(calls[1:], first['thread_id'], [])
    scoped = [e for e in history[len(failed_prefix):] if e.step_id == proposed.step_id]
    require(any(getattr(e.payload, 'state', None) is CollaborationStepState.CONFIRMED
                for e in scoped), 'Recovered consultation lacks fresh Confirm')
    replies = [e for e in scoped if isinstance(e.payload, CollaborationSpecialistReplyPayload)]
    syntheses = [e for e in scoped if isinstance(e.payload, CollaborationLeadSynthesisPayload)]
    results = [e.payload for e in scoped if isinstance(e.payload, CollaborationExecutionResult)]
    require(len(replies) == len(syntheses) == len(results) == 1,
            'Recovered reply synthesis or execution missing')
    reply, synthesis, execution = replies[0], syntheses[0].payload, results[0]
    assignment = proposed.proposal.assignments[0]
    require(reply.sender.agent_id == 'character_custodian'
            and reply.payload.assignment_id == assignment.assignment_id
            and reply.payload.input_ids == assignment.input_ids,
            'Recovered reply attribution differs')
    for payload in (reply.payload, synthesis, execution):
        require(payload.proposal_id == proposed.proposal.proposal_id
                and payload.proposal_revision == proposed.proposal.proposal_revision
                and payload.proposal_digest == proposed.proposal.proposal_digest,
                'Recovered proposal correlation differs')
    require(reply.payload.execution_attempt_id == synthesis.execution_attempt_id
            == execution.execution_attempt_id, 'Recovered execution correlation differs')
    require(synthesis.reply_event_ids == (reply.event_id,) and synthesis.next_intent is None,
            'Recovered synthesis attribution or terminal intent differs')
    assert_scoped_synthesis(proposed.proposal, replies, synthesis)
    require(execution.overall_outcome.value == 'succeeded'
            and execution.synthesis_outcome.value == 'succeeded'
            and execution.synthesis_result_event_id == syntheses[0].event_id
            and len(execution.assignment_outcomes) == 1
            and execution.assignment_outcomes[0].outcome.value == 'succeeded'
            and execution.assignment_outcomes[0].reply_event_id == reply.event_id,
            'Recovered execution result differs')
    return {'agent': 'character_custodian', 'fresh_thread': True,
            'physical_calls': len(calls), 'step_id': proposed.step_id}


class CallEvidence:
    """One serial-case budget; runtime's sibling calls may run concurrently."""

    def __init__(self, directory: Path, spec: dict, global_root: Path):
        self.directory = directory
        self.spec = spec
        self.global_root = Path(os.environ.get('STORY_LIVE_BUDGET_ROOT', str(global_root))).resolve()
        self.source_hashes = source_closure()
        self.run_budget = load_inventory()['budget']
        require(0 < self.run_budget['matrix_max_calls'] <= self.run_budget['max_calls'] <= 312,
                'Declared provider cap exceeds the absolute authorization')
        save(directory / 'source-closure.sanitized.json', self.source_hashes)
        self.prior_submissions = int(os.environ.get('STORY_LIVE_PRIOR_CASE_SUBMISSIONS', '0'))
        require(self.prior_submissions >= 0, 'Invalid historical call count')
        self.effective_case_cap = self.prior_submissions + spec['max_calls']
        save(directory / 'run-configuration.sanitized.json', {
            'budget': self.run_budget, 'fresh_case_cap': spec['max_calls'],
            'prior_case_submissions': self.prior_submissions,
            'effective_cumulative_case_cap': self.effective_case_cap})
        self.calls: list[dict] = []
        self.fault: str | None = None
        self.fault_agent: str | None = None
        self.fault_consumed = False
        self.budget_history = None
        self.specialist_done = asyncio.Event()
        self.held_submission = asyncio.Event()
        self.release_held = asyncio.Event()
        self.processes: dict[int, Any] = {}
        self.patches: list[Any] = []

    def install(self) -> None:
        for vendor, module in (('antigravity', antigravity_adapter), ('codex', codex_adapter)):
            original = module.run_process

            async def capture(request, *, _vendor=vendor, _original=original, **kwargs):
                return await self.invoke(_vendor, _original, request, kwargs)
            patch = mock.patch.object(module, 'run_process', new=capture)
            patch.start()
            self.patches.append(patch)

    def close(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        require(not self.processes, 'Owned provider process still active')

    def reserve(self) -> tuple[int, Path]:
        require(source_closure() == self.source_hashes, 'Accepted source/configuration changed before submission')
        require(len(self.calls) + self.prior_submissions < self.effective_case_cap,
                'Per-case physical-call cap across preserved attempts reached')
        ledger = self.global_root / 'call-budget.json'
        value = json.loads(ledger.read_text()) if ledger.exists() else {'reserved': 0, 'started_at': time.time()}
        require(value['reserved'] < self.run_budget['matrix_max_calls'], 'Approved matrix physical-call cap reached')
        require(time.time() - value['started_at'] < self.run_budget['seconds'], 'Global live wall-time budget reached')
        value['run_configuration'] = self.run_budget
        value['reserved'] += 1
        save(ledger, value)
        number = len(self.calls) + 1
        directory = self.directory / 'calls' / f'call-{number:03}'
        directory.mkdir(parents=True)
        return (number, directory)

    async def invoke(self, vendor, original, request, kwargs):
        require(os.environ.get('STORY_LIVE_SETUP_ONLY') != '1',
                'Setup-only guard blocked a model invocation')
        number, directory = self.reserve()
        argv = list(request.command)
        native_agent = argv[argv.index('--agent') + 1] if '--agent' in argv else 'lead_orchestrator'
        agent = next((semantic_id for semantic_id in ROSTER
                      if native_agent == semantic_id or native_agent.startswith(semantic_id + '-')),
                     native_agent)
        payload = argv[argv.index('--print') + 1].encode() if '--print' in argv else request.stdin
        record = dict(number=number, vendor=vendor, agent=agent, directory=str(directory), payload_bytes=len(payload), submitted=False, fault=None, model=argv[argv.index('--model') + 1] if '--model' in argv else None, resumed='--conversation' in argv or 'resume' in argv)
        schema = (json.loads(argv[argv.index('--json-schema') + 1])
                  if vendor == 'antigravity' else
                  json.loads(Path(argv[argv.index('--output-schema') + 1]).read_bytes()))
        record['schema_semantic_sha256'] = semantic_digest(schema)
        record['payload_sha256'] = digest(payload)
        record['payload_semantic_sha256'] = semantic_digest(json.loads(payload))
        record['prior_thread_id'] = (argv[argv.index('--conversation') + 1]
                                     if '--conversation' in argv else
                                     argv[-2] if 'resume' in argv else None)
        execution_workspace = request.execution_workspace
        effective_cwd = (execution_workspace.worktree_root
                         if vendor == 'antigravity' and execution_workspace is not None
                         else request.cwd)
        self.calls.append(record)
        save(directory / 'summary.sanitized.json', record)
        save(directory / 'command.private.json', dict(argv=argv, cwd=str(effective_cwd), timeout=request.timeout_seconds))
        (directory / 'stdin.bin').write_bytes(request.stdin)
        if vendor == 'antigravity':
            require(argv[argv.index('--output-format') + 1] == 'json', 'Antigravity JSON transport absent')
            require(request.stdin == b'', 'Unexpected Antigravity stdin transport')
            require(execution_workspace is not None, 'Antigravity execution workspace absent')
            staged = effective_cwd / '.agents/agents' / native_agent / 'agent.md'
            body = staged.read_bytes()
            require(b'through finish' in body, 'Current provider-local finish prompt absent')
            save(directory / 'staged.sanitized.json', {'sha256': digest(body), 'bytes': len(body)})
        native_create = kwargs.get('create_process', asyncio.create_subprocess_exec)
        selected_fault = self.fault if not self.fault_consumed and (self.fault_agent is None or self.fault_agent == agent) else None
        if selected_fault == 'budget_next_intent' and budget_synthesis_material(json.loads(payload)) is None:
            selected_fault = None
        if selected_fault is not None:
            self.fault_consumed = True
            record['fault'] = selected_fault

        async def tracked_create(*args, **options):
            process = await native_create(*args, **options)
            self.processes[process.pid] = process
            record['submitted'] = True
            save(directory / 'summary.sanitized.json', record)
            if selected_fault == 'hold_for_cancel':
                import signal
                os.kill(process.pid, signal.SIGSTOP)
                record['stopped_pid'] = process.pid
                self.held_submission.set()
            return process
        kwargs['create_process'] = tracked_create
        started = time.monotonic()
        try:
            actual = await original(request, **kwargs)
        except BaseException as exc:
            record.update(exception_type=type(exc).__name__, seconds=time.monotonic() - started)
            save(directory / 'summary.sanitized.json', record)
            raise
        finally:
            for pid, process in list(self.processes.items()):
                if process.returncode is not None:
                    self.processes.pop(pid)
        (directory / 'stdout.raw').write_bytes(actual.stdout)
        (directory / 'stderr.raw').write_bytes(actual.stderr)
        record.update(returncode=actual.returncode, seconds=time.monotonic() - started, stdout_sha256=digest(actual.stdout), stderr_sha256=digest(actual.stderr))
        if vendor == 'antigravity':
            try:
                value = json.loads(actual.stdout)
                record.update(native_status=value.get('status'), native_turns=value.get('num_turns'), thread_id=value.get('conversation_id'), action=value.get('structured_output', {}).get('action', {}).get('kind'), schema_order_equal=canonical_provider_json(value.get('json_schema')) == argv[argv.index('--json-schema') + 1].encode())
                save(directory / 'terminal.private.json', value)
            except (ValueError, TypeError, AttributeError):
                record['unparseable_native_json'] = True
        if vendor == 'codex':
            events = [json.loads(line) for line in actual.stdout.splitlines() if line.strip()]
            threads = [e['thread_id'] for e in events if e.get('type') == 'thread.started']
            if threads:
                record['thread_id'] = threads[-1]
            output = Path(argv[argv.index('--output-last-message') + 1])
            if output.exists():
                (directory / 'codex-response.raw').write_bytes(output.read_bytes())
        delivered = actual
        if selected_fault == 'budget_next_intent':
            require(vendor == 'antigravity' and callable(self.budget_history),
                    'Budget fault has no actual runtime history owner')
            spent = project_collaboration_history(self.budget_history()).physical_calls_spent
            stdout, proof = inject_budget_next_intent(
                actual.stdout, json.loads(payload), agent=agent,
                returncode=actual.returncode, physical_calls_spent=spent)
            delivered = dataclasses.replace(actual, stdout=stdout)
            save(directory / 'injected-delta.sanitized.json', proof)
        if selected_fault in {'nonzero', 'utf8', 'json', 'unrelated_error', 'schema', 'known_error'}:
            require(actual.returncode == 0, 'Actual call failed before the designated fault injection')
            if selected_fault == 'nonzero':
                delivered = dataclasses.replace(actual, returncode=17)
            elif selected_fault == 'utf8':
                delivered = dataclasses.replace(actual, stdout=b'\xff')
            elif selected_fault == 'json':
                delivered = dataclasses.replace(actual, stdout=b'{')
            else:
                value = json.loads(actual.stdout)
                require(value['status'] == 'SUCCESS', 'Injection requires a real successful completion')
                if selected_fault == 'schema':
                    value['json_schema'] = {'type': 'null'}
                else:
                    value['status'] = 'ERROR'
                    value['error'] = 'synthetic unrelated native error' if selected_fault == 'unrelated_error' else 'Your previous response contained an improperly formatted function call. Please retry with a properly formatted function call. Retries remaining: 2'
                delivered = dataclasses.replace(actual, stdout=canonical_provider_json(value))
            (directory / 'delivered-fault.stdout').write_bytes(delivered.stdout)
            save(directory / 'injected-delta.sanitized.json', {'fault': selected_fault, 'returncode': delivered.returncode, 'stdout_sha256': digest(delivered.stdout)})
        (directory / 'delivered.stdout').write_bytes(delivered.stdout)
        (directory / 'delivered.stderr').write_bytes(delivered.stderr)
        record['delivered_returncode'] = delivered.returncode
        save(directory / 'summary.sanitized.json', record)
        if agent != 'lead_orchestrator':
            self.specialist_done.set()
        return delivered

    def resume_stopped_processes(self):
        import signal
        for record in self.calls:
            pid = record.get('stopped_pid')
            if pid is not None and pid in self.processes:
                os.kill(pid, signal.SIGCONT)

class ConversationCase:

    def __init__(self, spec, directory: Path, global_root: Path):
        self.spec = spec
        self.directory = directory
        self.session = 'live'
        self.model = MODEL_BY_CASE.get(spec['id'], 'gemini-3.8-flash-low')
        self.backend = None
        self.runtime = None
        self.turns = []
        self.observer = CallEvidence(directory, spec, global_root)
        self.conversation = None
        self.initial_artifacts = {}

    def setup(self):
        os.environ.update(GIT_AUTHOR_NAME='Story Live Test', GIT_AUTHOR_EMAIL='story-live@example.invalid', GIT_COMMITTER_NAME='Story Live Test', GIT_COMMITTER_EMAIL='story-live@example.invalid')
        from story_writing_agents.workspace import Workspace
        from story_writing_agents.workspace.initialization import provision_session_resources
        main_workspace = initialize_workspace(self.directory / 'workspace')
        resources = provision_session_resources(main_workspace.root, self.session)
        self.workspace = Workspace(resources.identity.worktree_root)
        from story_writing_agents.workspace import WORKSPACE_DIRECTORIES
        for relative in WORKSPACE_DIRECTORIES:
            self.workspace.path(relative).mkdir(parents=True, exist_ok=True)
        save(self.directory / 'workspace.private.json', {'main': str(main_workspace.root),
                                                       'session': str(self.workspace.root)})
        repository = Path(__file__).resolve().parents[1]
        require(self.workspace.path('config/agents/common/AGENTS.md').read_bytes() == (repository / 'config/agents/common/AGENTS.md').read_bytes(), 'Workspace common prompt differs from current source')
        self.seed_fixtures()
        if self.spec['id'] == 'L05-MIXED':
            from story_writing_agents.config import ConfigManager
            manager = ConfigManager(self.workspace)
            baseline = manager.load_configuration_baseline()
            configs = dict(baseline.configuration.agent_configs)
            configs['plot_architect'] = dataclasses.replace(configs['plot_architect'], antigravity_model='gemini-3.1-pro-high')
            configs['character_custodian'] = dataclasses.replace(configs['character_custodian'], antigravity_model='claude-sonnet-4-6')
            manager.commit_configuration(baseline, dataclasses.replace(baseline.configuration, agent_configs=configs))
        self.open_backend(create=True)
        self.git_baseline = git_snapshot(self.workspace.root)
        save(self.directory / 'git-baseline.sanitized.json', self.git_baseline)
        self.conversation = self.runtime.conversation_storage_services.catalog.create(self.session, self.spec['id']).conversation_id
        self.observer.install()
        save(self.directory / 'case.private.json', self.spec)

    def open_backend(self, *, create=False):
        overrides = {agent: 'antigravity' for agent in ROSTER}
        if self.spec['id'] == 'L05-MIXED':
            overrides['lead_orchestrator'] = 'codex'
        self.backend = AgentBackend(self.workspace, executable=os.environ.get('STORY_CODEX_EXECUTABLE', 'codex'), antigravity_executable=os.environ.get('STORY_ANTIGRAVITY_EXECUTABLE', 'agy'), antigravity_model=None if self.spec['id'] == 'L05-MIXED' else self.model, timeout_seconds=180, backend_overrides=overrides)
        if create:
            self.backend.provision_session_resources(self.session)
            self.backend.create_session_with_access(self.session)
        else:
            self.backend.acquire_session_access(self.session)
        self.runtime = StoryRuntime(self.workspace, self.backend)

    def seed_fixtures(self):
        if self.spec['id'] == 'L06A-QUESTION':
            return
        values = {'core_documents/global_outline.md': OUTLINE, 'core_documents/character_sheets.md': CHARACTERS, 'manuscript/part-01/opening.md': DRAFT if self.spec['id'] not in {'L02-PRO-LOW-ONE', 'L04-SONNET-ONE'} else CONSISTENT_CANDIDATE, 'manuscript/part-02/schedule.md': 'ORBIT623: the dispatch is scheduled for day 4.\n'}
        from story_writing_agents.writing_artifacts import (
            ArtifactCreateOperation, ArtifactReplaceOperation,
            build_artifact_change_set, canonical_artifact_kind,
            project_artifact_change_set,
        )
        from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
        from story_writing_agents.git.story_commit_contracts import (
            StoryCommitApplicationEvidence, StoryCommitConfirmation,
            StoryCommitDecision, StoryCommitState,
        )
        from story_writing_agents.workspace.initialization import compose_story_commit_service
        repository = WritingArtifactRepository(self.workspace.root)
        base = {name: self.workspace.path(name).read_bytes() for name in repository.list_paths()}
        operations = []
        for name, value in sorted(values.items()):
            content = value.encode()
            kind = canonical_artifact_kind(name)
            if name in base:
                operation = ArtifactReplaceOperation(name, kind, digest(base[name]), content, digest(content))
            else:
                operation = ArtifactCreateOperation(name, kind, content, digest(content))
            operations.append(operation)
        change_set = build_artifact_change_set(base, tuple(operations))
        for name, content in project_artifact_change_set(base, change_set).items():
            path = self.workspace.path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        require({name: self.workspace.path(name).read_bytes() for name in repository.list_paths()}
                == project_artifact_change_set(base, change_set), 'Fixture application bytes differ')
        evidence = StoryCommitApplicationEvidence(
            self.session, 'fixture-application', 'fixture-proposal', 'fixture-correlation',
            change_set, change_set.projected_state_digest,
        )
        service = compose_story_commit_service(self.workspace.root, self.session)
        candidate = service.prepare(evidence, 'Prepare synthetic conversation fixture',
                                    ('Supply exact synthetic facts for live consultation evidence.',))
        confirmation = StoryCommitConfirmation(
            'fixture-confirmation', candidate.candidate_id, candidate.proposal_id,
            candidate.correlation_id, StoryCommitDecision.COMMIT,
        )
        result = service.execute(service.authorize(candidate, confirmation))
        require(result.state is StoryCommitState.COMMITTED, 'Fixture owner commit did not complete')
        save(self.directory / 'fixture-commit.private.json', dataclasses.asdict(result))
        self.initial_artifacts = {name: digest(self.workspace.path(name).read_bytes()) for name in values}
        if self.spec['id'] == 'F02-LOST-AUTHORITY':
            assert_seeded_artifacts(self.workspace, self.initial_artifacts)
            save(self.directory / 'initial-artifacts.private.json', self.initial_artifacts)

    def history(self):
        return self.runtime.conversation_storage_services.event_store.read(self.session, self.conversation)

    async def lead(self, text):
        result = await self.runtime.lead_turn(self.session, self.conversation, text)
        self.turns.append(result)
        save(self.directory / f'turn-{len(self.turns):02}.private.json', dataclasses.asdict(result))
        require(result.message != '', 'Missing visible Lead message')
        return result

    async def propose(self, agents=('plot_architect',), *, text=None, references=True):
        request = consultation_request(self.spec, agents, text=text, references=references)
        before = len(self.observer.calls)
        result = await self.lead(request)
        require(result.kind is LeadOutcomeKind.PROPOSAL and result.state is CollaborationStepState.AWAITING_CONFIRMATION, 'Required live proposal branch was not observed')
        assert_review_proposal(result.proposal, agents)
        require(all((c['agent'] == 'lead_orchestrator' for c in self.observer.calls[before:])), 'Unconfirmed specialist call')
        return result

    async def confirm(self, proposed):
        exact = decision(proposed)
        save(self.directory / f'decision-{len(self.turns):02}.private.json', dataclasses.asdict(exact))
        result = await self.runtime.decide_lead_step(exact)
        self.turns.append(result.turn)
        return result.turn

    async def rejects_decision(self, exact):
        before = len(self.observer.calls)
        history = self.history()
        try:
            await self.runtime.decide_lead_step(exact)
        except ValueError:
            pass
        else:
            raise AssertionError('Stale or forged confirmation was accepted')
        require(len(self.observer.calls) == before and self.history() == history, 'Rejected decision changed authority or invoked a provider')

    def verify_completed(self, turn, expected_agents, *, rounds=1):
        require(turn.state is CollaborationStepState.COMPLETED, 'Consultation did not complete')
        return self.audit_completed_history(expected_agents, rounds=rounds)

    def audit_completed_history(self, expected_agents, *, rounds=1):
        history = self.history()
        replies = [e for e in history if isinstance(e.payload, CollaborationSpecialistReplyPayload)]
        syntheses = [e.payload for e in history if isinstance(e.payload, CollaborationLeadSynthesisPayload)]
        require(len(replies) == len(expected_agents) * rounds, 'Wrong number of complete specialist replies')
        require(tuple(e.sender.agent_id for e in replies) == tuple(expected_agents) * rounds,
                'Persisted specialist reply attribution differs from exact roster')
        require(len(syntheses) == rounds, 'Actual Lead synthesis missing')
        require(syntheses[-1].next_intent is None, 'Unexpected further round proposal')
        for synthesis in syntheses:
            matching = [e for e in replies if e.payload.proposal_revision == synthesis.proposal_revision]
            require(tuple((e.event_id for e in matching)) == synthesis.reply_event_ids, 'Synthesis attribution order differs from persisted replies')
            proposals = [e.payload for e in history if isinstance(e.payload, CollaborationProposal)
                         and e.payload.proposal_id == synthesis.proposal_id
                         and e.payload.proposal_revision == synthesis.proposal_revision]
            require(len(proposals) == 1, 'Synthesis lacks exact durable proposal')
            assert_scoped_synthesis(proposals[0], matching, synthesis)
        executions = [e.payload for e in history if isinstance(e.payload, CollaborationExecutionResult)]
        require(executions and all((e.overall_outcome.value == 'succeeded' for e in executions)), 'Execution outcome did not complete')
        projection = project_collaboration_history(history)
        require(projection.current_state is CollaborationStepState.COMPLETED, 'Durable projection disagrees with completion')
        return replies

    async def reopen_followup(self):
        self.backend.close()
        self.open_backend()
        before = len(self.observer.calls)
        prior = [call for call in self.observer.calls if call['agent'] == 'lead_orchestrator']
        reply = await self.lead(WORKFLOW_FOLLOWUP)
        current = self.observer.calls[before:]
        require(current and current[0]['resumed'], 'Reopened follow-up failed to resume provider state')
        if prior and prior[-1].get('thread_id'):
            argv = json.loads((Path(current[0]['directory']) / 'command.private.json').read_text())['argv']
            require(current[0]['prior_thread_id'] == prior[-1]['thread_id'], 'Reopened follow-up used another provider thread')
        require(reply.kind is LeadOutcomeKind.ANSWER and MARKER in reply.message, 'Live resumed follow-up did not recall marker')
        assert_workflow_judgment(self.history(), reply)
        assert_resumed_call_chain(current, prior[-1]['thread_id'], audit_case_exchanges(self))
        return reply

    async def normal_consultation(self):
        agents = AGENTS_BY_CASE[self.spec['id']]
        proposed = await self.propose(agents, text=self.spec['instruction'] + ' The recall marker is ' + MARKER + '.')
        completed = await self.confirm(proposed)
        replies = self.verify_completed(completed, agents)
        await self.rejects_decision(decision(proposed))
        await self.reopen_followup()
        if self.spec['id'] == 'L02-PRO-LOW-ONE':
            require(replies[0].payload.assessment.value == 'supports', 'Required natural supports assessment absent')
        if self.spec['id'] == 'L03-FLASH-THREE':
            require({e.payload.assessment.value for e in replies} >= {'conflict', 'concern', 'insufficient_evidence'}, 'Required natural specialist assessment branches absent')

    def final_checks(self):
        history = self.history()
        baseline = json.loads((self.directory / 'git-baseline.sanitized.json').read_text())
        require(git_snapshot(self.workspace.root) == baseline, 'Conversation changed Git HEAD or index')
        physical = [e.payload for e in history if isinstance(e.payload, CollaborationProviderCallPayload)]
        if self.spec['id'] != 'F05-DIAGNOSTICS-RECOVERY':
            require(sum((e.state.value == 'submitted' for e in physical)) == sum((c['submitted'] for c in self.observer.calls)), 'Durable submitted-call accounting differs from actual processes')
        for name, before in self.initial_artifacts.items():
            if self.spec['id'] != 'F01-REFERENCE-DRIFT':
                require(digest(self.workspace.path(name).read_bytes()) == before, 'Conversation changed an artifact')
        exchanges = []
        for path in self.workspace.path('debug_logs/agent_exchanges/live').glob('*.jsonl'):
            records = [json.loads(line) for line in path.read_text().splitlines()]
            exchanges.extend(records)
            require(all(not r['content_truncated'] for r in records),
                    'Live evidence was truncated')
        audit = audit_exchanges(exchanges, self.observer.calls, history)
        if self.spec['id'] == 'F05-DIAGNOSTICS-RECOVERY':
            require(not physical, 'Direct-adapter case fabricated conversation call events')
            outcomes = json.loads((self.directory / 'diagnostic-outcomes.sanitized.json').read_text())
            require(len(audit) == len(outcomes) == 6, 'Direct diagnostic call inventory differs')
            for row, expected in zip(audit, outcomes):
                require(row['outcome'] == ('accepted' if expected['accepted'] else 'provider_failure'),
                        'Direct diagnostic outcome differs from actual adapter acceptance')
        save(self.directory / 'call-exchange-audit.sanitized.json', audit)
        if self.spec['id'] == 'L05-MIXED':
            require(any((c['vendor'] == 'codex' and c['agent'] == 'lead_orchestrator' for c in self.observer.calls)), 'Actual mixed Codex Lead missing')
            for agent, model in (('plot_architect', 'gemini-3.1-pro-high'),
                                 ('character_custodian', 'claude-sonnet-4-6')):
                calls = [c for c in self.observer.calls if c['agent'] == agent]
                require(calls and all(c['vendor'] == 'antigravity' and c['model'] == model for c in calls),
                        'Mixed specialist model routing differs from declared matrix')
        save(self.directory / 'history.private.json', canonical_history(history))
        save(self.directory / 'exchange-index.sanitized.json', [{k: r[k] for k in ('exchange_id', 'agent_id', 'request_type', 'sequence', 'content_sha256')} for r in exchanges])
        return dict(lead_outcomes=sorted({t.kind.value for t in self.turns}), assessments=sorted({e.payload.assessment.value for e in history if isinstance(e.payload, CollaborationSpecialistReplyPayload)}), physical_calls=len(self.observer.calls), state=project_collaboration_history(history).current_state)

    def close(self):
        self.observer.resume_stopped_processes()
        if self.backend is not None:
            self.backend.close()
        self.observer.close()

    async def decisions(self):
        text = self.spec['instruction']
        first = await self.propose(text=text, references=False)
        before = len(self.observer.calls)
        rejected = await self.runtime.decide_lead_step(decision(first, CollaborationConfirmationDecision.REJECT))
        require(rejected.turn.state is CollaborationStepState.REJECTED and len(self.observer.calls) == before, 'Reject dispatched work')
        second = await self.propose(text=text, references=False)
        await self.rejects_decision(decision(second, proposal_digest='sha256:' + '0' * 64))
        revised = await self.runtime.decide_lead_step(decision(second, CollaborationConfirmationDecision.REVISE, revision_request='Keep only plot_architect, but focus the pending review on chronology. Return the revised consultation proposal and wait.'))
        self.turns.append(revised.turn)
        require(revised.turn.kind is LeadOutcomeKind.PROPOSAL, 'Required live revised proposal absent')
        assert_review_proposal(revised.turn.proposal, ('plot_architect',))
        require(revised.turn.proposal.authoring_scope.task_id == second.proposal.authoring_scope.task_id
                and revised.turn.proposal.authoring_scope.task_revision == second.proposal.authoring_scope.task_revision + 1,
                'Revised review lost the offered task revision')
        require(revised.turn.proposal.proposal_revision > second.proposal.proposal_revision, 'Revision did not increase')
        await self.rejects_decision(decision(second))
        before = len(self.observer.calls)
        await self.lead('Yes. This is ordinary conversation; tell me which structured action would confirm, without executing specialist work.')
        require(all((c['agent'] == 'lead_orchestrator' for c in self.observer.calls[before:])), 'Free text authorized a specialist')
        await self.rejects_decision(decision(revised.turn))

    async def second_round(self):
        proposed = await self.propose(text=self.spec['instruction'], references=False)
        second = await self.confirm(proposed)
        require(second.kind is LeadOutcomeKind.PROPOSAL and second.state is CollaborationStepState.AWAITING_CONFIRMATION, 'Real focused second-round proposal not observed')
        require(second.step_id == proposed.step_id and second.proposal.budget.rounds_spent == 1, 'Round lineage not preserved')
        require(second.proposal.proposal_revision == proposed.proposal.proposal_revision + 1, 'Second round revision differs')
        assert_review_proposal(second.proposal, ('plot_architect',))
        require((second.proposal.authoring_scope.task_id, second.proposal.authoring_scope.task_revision)
                == (proposed.proposal.authoring_scope.task_id, proposed.proposal.authoring_scope.task_revision),
                'Second review round changed current task/revision')
        replies = {e.event_id for e in self.history() if isinstance(e.payload, CollaborationSpecialistReplyPayload)}
        require(any((i.input_id in replies for i in second.proposal.basis.inputs)), 'Second-round reply input absent')
        await self.rejects_decision(decision(proposed))
        completed = await self.confirm(second)
        self.verify_completed(completed, ('plot_architect',), rounds=2)
        require(second.proposal.budget.physical_calls_spent + second.proposal.budget.max_additional_physical_calls <= 12, 'Lineage exceeds twelve-call budget')

    async def reference_drift(self):
        proposed = await self.propose()
        self.workspace.path('core_documents/global_outline.md').write_text(OUTLINE + 'External fixture drift.\n')
        before = len(self.observer.calls)
        try:
            outcome = await self.runtime.decide_lead_step(decision(proposed))
        except BackendProtocolError as exc:
            if str(exc) != 'lead proposal authority drift requires regeneration and confirmation':
                raise
            outcome = None
        require(len(self.observer.calls) == before, 'Reference drift launched specialist work')
        if outcome is not None:
            require(outcome.turn.state is not CollaborationStepState.COMPLETED, 'Stale reference was accepted')

    async def lost_authority_child(self):
        proposed = await self.propose(references=False)
        save(self.directory / 'confirmed-proposal.private.json', dataclasses.asdict(proposed))
        store = self.runtime.conversation_storage_services.event_store
        original = store.append_batch

        def append_then_stop(batch):
            result = original(batch)
            if any((getattr(e.payload, 'state', None) is CollaborationStepState.CONFIRMED for e in batch.events)):
                save(self.directory / 'lost-authority-checkpoint.sanitized.json', {'session': self.session, 'conversation': self.conversation, 'calls': len(self.observer.calls), 'stage': 'confirmed_before_running', 'artifact_baseline_sha256': digest((self.directory / 'initial-artifacts.private.json').read_bytes())})
                os._exit(86)
            return result
        with mock.patch.object(store, 'append_batch', side_effect=append_then_stop):
            await self.confirm(proposed)
        raise AssertionError('Lost-authority barrier not reached')

    async def lost_authority_resume(self):
        checkpoint = json.loads((self.directory / 'lost-authority-checkpoint.sanitized.json').read_text())
        from story_writing_agents.workspace import Workspace
        paths = json.loads((self.directory / 'workspace.private.json').read_text())
        self.workspace = Workspace(paths['session'])
        self.initial_artifacts = restore_artifact_baseline(self.directory, self.workspace, checkpoint)
        self.open_backend()
        self.conversation = checkpoint['conversation']
        self.observer.install()
        before = len(self.observer.calls)
        reconfirm = await self.lead('Resume the pending consultation safely.')
        require(reconfirm.state is CollaborationStepState.RECONFIRMATION_REQUIRED and len(self.observer.calls) == before, 'Restart did not require local reconfirmation')
        old = json.loads((self.directory / 'confirmed-proposal.private.json').read_text())
        require(reconfirm.proposal.proposal_digest == old['proposal']['proposal_digest'], 'Reconfirmation changed proposal')
        require(reconfirm.confirmation_request_id != old['confirmation_request_id'], 'Restart reused authority')
        require(reconfirm.evidence == reconfirm.proposal.basis.evidence
                and json.loads(json.dumps(dataclasses.asdict(reconfirm.evidence), default=str)) == old['evidence'],
                'Reconfirmation lost exact proposal evidence')
        require(json.loads(json.dumps(dataclasses.asdict(reconfirm.proposal), default=str)) == old['proposal'],
                'Reconfirmation changed the preserved proposal')
        await self.rejects_decision(decision(
            reconfirm, confirmation_request_id=old['confirmation_request_id']))
        completed = await self.confirm(reconfirm)
        self.verify_completed(completed, ('plot_architect',))
        assert_seeded_artifacts(self.workspace, self.initial_artifacts)

    async def interrupted_or_failed(self, *, cancel):
        agents = ('plot_architect', 'character_custodian')
        proposed = await self.propose(agents, references=False)
        self.observer.fault = 'hold_for_cancel' if cancel else 'nonzero'
        self.observer.fault_agent = 'character_custodian'
        complete_assignment = asyncio.Event()
        original = self.runtime._run_specialist_assignment

        async def tracked_assignment(**kwargs):
            result = await original(**kwargs)
            if kwargs['assignment'].specialist_id == 'plot_architect':
                complete_assignment.set()
            return result
        with mock.patch.object(self.runtime, '_run_specialist_assignment', new=tracked_assignment):
            task = asyncio.create_task(self.confirm(proposed))
            if cancel:
                await asyncio.wait_for(self.observer.held_submission.wait(), 180)
                await asyncio.wait_for(complete_assignment.wait(), stage_seconds(next(
                assignment.max_physical_calls for assignment in proposed.proposal.assignments
                if assignment.specialist_id == 'plot_architect')))
                task.cancel()
                self.observer.resume_stopped_processes()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError('Outer cancellation did not propagate')
            else:
                await task
        require(self.observer.fault_consumed, 'Fault was never applied to an actual call')
        history = self.history()
        executions = [e.payload for e in history if isinstance(e.payload, CollaborationExecutionResult)]
        require(executions, 'Partial execution result missing')
        last = executions[-1]
        require(last.overall_outcome.value == ('cancelled' if cancel else 'failed'), 'Wrong terminal execution outcome')
        require(any((o.outcome.value == 'succeeded' for o in last.assignment_outcomes)), 'Successful sibling result lost')
        require(not any((isinstance(e.payload, CollaborationLeadSynthesisPayload) for e in history)), 'Partial failure incorrectly synthesized')
        self.observer.fault = None
        if cancel:
            self.backend.close()
            self.open_backend()
            before = len(self.observer.calls)
            finalized = await self.lead('Inspect the already durable interrupted result; do not replay it.')
            require(finalized.state is CollaborationStepState.INTERRUPTED and len(self.observer.calls) == before, 'Interrupted execution replayed')
        else:
            await self.recover_failed_character(proposed)

    async def recover_failed_character(self, failed_proposal):
        failed_prefix = canonical_history(self.history())
        faulted = [c for c in self.observer.calls
                   if c['agent'] == 'character_custodian' and c['fault'] == 'nonzero']
        require(len(faulted) == 1, 'Expected one actual faulted character call')
        before = len(self.observer.calls)
        proposed = await self.propose(
            ('character_custodian',), references=False,
            text=self.spec['recovery_instruction'],
        )
        require(proposed.proposal.authoring_scope.task_id != failed_proposal.proposal.authoring_scope.task_id,
                'Independent recovery reused failed authoring task')
        require(proposed.step_id != failed_proposal.step_id
                and proposed.proposal.budget.rounds_spent == 0
                and proposed.confirmation_request_id != failed_proposal.confirmation_request_id,
                'Recovery reused failed step or confirmation authority')
        require(all(c['agent'] == 'lead_orchestrator' for c in self.observer.calls[before:]),
                'Recovery dispatched before Confirm')
        completed = await self.confirm(proposed)
        audit = assert_character_recovery(
            self.history(), failed_prefix, faulted[0], self.observer.calls[before:],
            proposed, completed,
        )
        require(project_collaboration_history(self.history()).current_state
                is CollaborationStepState.COMPLETED, 'Recovery projection did not complete')
        save(self.directory / 'character-recovery.sanitized.json', audit)

    async def diagnostic_cases(self):
        from story_writing_agents.prompts import PromptAssembler
        from story_writing_agents.provider_adapter import ProviderInvocation, ProviderInvocationError
        from story_writing_agents.workspace.initialization import compose_session_artifact_binding
        from story_writing_agents.provider_wire import add_contract_guidance, describe_schema
        schema = describe_schema({"type": "object", "additionalProperties": False,
            "description": "Complete the diagnostic request with a supports_proposal specialist result and brief nonempty response_text.",
            "properties": {"agent_action": {"type": "object", "additionalProperties": False,
                "properties": {"action_type": {"type": "string", "enum": ["submit_result"]},
                    "task_result": {"type": "object", "additionalProperties": False,
                        "properties": {"response_type": {"type": "string", "enum": ["specialist_assignment_result"]},
                            "assignment_status": {"type": "string", "enum": ["supports_proposal"]},
                            "response_text": {"type": "string", "minLength": 1}},
                        "required": ["response_type", "assignment_status", "response_text"]}},
                "required": ["action_type", "task_result"]}}, "required": ["agent_action"]})
        definition = PromptAssembler(self.workspace, backend_overrides={'plot_architect': 'antigravity'}).build_contextual_definition(session_id='diagnostics', agent_id='plot_architect')
        session_binding = compose_session_artifact_binding(
            self.workspace.root,
            self.session,
        )
        outcomes = []
        for index, fault in enumerate((None, 'known_error', 'utf8', 'json', 'unrelated_error', 'schema')):
            adapter = antigravity_adapter.AntigravityAdapter(executable=os.environ.get('STORY_ANTIGRAVITY_EXECUTABLE', 'agy'), timeout_seconds=180, state_root=self.directory / f'diagnostic-state-{index}', explicit_model=self.model, explicit_effort=None)
            binding = adapter.bind(definition)
            role = adapter.session_role_projection(binding)
            with session_binding.open_ready_resources() as access:
                access.install_provider_role(role)
            with session_binding.open_provider_execution_workspace() as execution_workspace:
                payload = add_contract_guidance({"request_correlation_token": "synthetic-correlation",
                "task_input": {"workflow_type": "specialist_task", "user_request_text":
                    "Return the declared specialist_assignment_result with assignment_status supports_proposal and a brief response_text."}}, schema)
                invocation = ProviderInvocation(session_id=self.session, schema=antigravity_adapter.AntigravityAdapter.native_output_schema(schema), payload=payload, thread_id=None, binding_components=binding.binding_components, execution_workspace=execution_workspace)
                self.observer.fault = fault
                self.observer.fault_agent = 'plot_architect'
                self.observer.fault_consumed = False
                from story_writing_agents.agent_exchanges import AgentExchangeProvider, AgentExchangeScopeKind
                capture = self.backend._agent_exchange_capture.begin(session_id=self.session, scope_kind=AgentExchangeScopeKind.CONVERSATION, scope_id=self.conversation, agent_id='plot_architect', provider=AgentExchangeProvider.ANTIGRAVITY, invocation=invocation)
                try:
                    result = await adapter.invoke(binding, invocation)
                except ProviderInvocationError as exc:
                    from story_writing_agents.agent_exchanges.capture import AgentExchangeTerminalCode
                    self.backend._capture_provider_failure_without_replacing(capture, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, failure=exc)
                    require(fault not in (None, 'known_error'), 'Expected real/positively proven recovered completion failed')
                    require(exc.response_diagnostic is not None, 'Completed failure diagnostics missing')
                    outcomes.append({'fault': fault, 'accepted': False, 'diagnostic': str(exc)})
                else:
                    capture.record_response(result)
                    capture.accepted()
                    require(fault in (None, 'known_error'), 'Injected malformed/error result accepted')
                    answer = json.loads(result.response_text)
                    require(type(answer) is dict and set(answer) == {'agent_action'}, 'Direct diagnostic answer has incorrect outer shape')
                    action = answer['agent_action']
                    require(type(action) is dict and set(action) == {'action_type', 'task_result'} and action['action_type'] == 'submit_result', 'Direct diagnostic action differs')
                    assessment = action['task_result']
                    require(type(assessment) is dict and set(assessment) == {'response_type', 'assignment_status', 'response_text'} and assessment['response_type'] == 'specialist_assignment_result' and assessment['assignment_status'] == 'supports_proposal' and type(assessment['response_text']) is str and bool(assessment['response_text'].strip()), 'Direct diagnostic specialist result differs')
                    if fault == 'known_error':
                        require(result.response_diagnostic is not None, 'Recovered native envelope not retained')
                    outcomes.append({'fault': fault, 'accepted': True})
        save(self.directory / 'diagnostic-outcomes.sanitized.json', outcomes)

    async def budget_finalization(self):
        """Actual three-specialist round plus a named unaffordable intent delivery."""
        from story_writing_agents.conversations import CollaborationNextAction, CollaborationProposal

        proposed = await self.propose(BUDGET_REVIEWERS)
        self.observer.fault = 'budget_next_intent'
        self.observer.fault_agent = 'lead_orchestrator'
        self.observer.budget_history = self.history
        completed = await self.confirm(proposed)
        require(self.observer.fault_consumed, 'Required completed synthesis fault was not reached')
        require(completed.state is CollaborationStepState.COMPLETED
                and completed.next_action is CollaborationNextAction.CONTINUE_WITH_LEAD
                and completed.proposal is None and completed.confirmation_request_id is None,
                'Budget completion retained or invented continuation authority')
        history = self.history()
        replies = [event for event in history if isinstance(event.payload, CollaborationSpecialistReplyPayload)]
        syntheses = [event.payload for event in history if isinstance(event.payload, CollaborationLeadSynthesisPayload)]
        results = [event.payload for event in history if isinstance(event.payload, CollaborationExecutionResult)]
        proposals = [event.payload for event in history if isinstance(event.payload, CollaborationProposal)]
        require(len(replies) == 3 and tuple(event.sender.agent_id for event in replies) == BUDGET_REVIEWERS,
                'Budget case did not preserve all three actual specialist replies')
        require(len(syntheses) == len(results) == len(proposals) == 1
                and results[0].overall_outcome.value == 'succeeded',
                'Budget case has missing completion or an additional proposal')
        synthesis = syntheses[0]
        require(synthesis.reply_event_ids == tuple(event.event_id for event in replies),
                'Budget synthesis lost exact reply attribution')
        assert_scoped_synthesis(proposed.proposal, replies, synthesis)
        injected = [call for call in self.observer.calls if call['fault'] == 'budget_next_intent']
        require(len(injected) == 1, 'Budget case changed more than one actual completion')
        call = injected[0]
        proof = json.loads((Path(call['directory']) / 'injected-delta.sanitized.json').read_text())
        require(proof['physical_calls_spent'] + 1 == project_collaboration_history(history).physical_calls_spent
                == len(self.observer.calls), 'Budget injection used a different durable lineage count')
        delivered = json.loads((Path(call['directory']) / 'delivered.stdout').read_bytes())['structured_output']['agent_action']['task_result']
        from tests.provider_wire_fixture import result as wire_result
        expected_intent = wire_result({'next_intent': dataclasses.asdict(synthesis.next_intent)}, 'consultation-summary-v4')['follow_up_consultation']
        require(synthesis.message == delivered['response_text'] and expected_intent == delivered['follow_up_consultation'] and synthesis.evidence == proposed.proposal.basis.evidence, 'Durable budget synthesis differs from the exact delivered result')
        require('exceeds its remaining call budget' in completed.message,
                'Visible budget completion notice absent')
        require(project_collaboration_history(history).current_state is CollaborationStepState.COMPLETED
                and project_collaboration_history(history).physical_calls_spent <= 12,
                'Budget lineage did not terminate within the runtime bound')
        await self.rejects_decision(decision(proposed))
        before = len(self.observer.calls)
        self.backend.close()
        self.open_backend()
        require(self.history() == history and len(self.observer.calls) == before,
                'Reopening replayed or changed completed budget work')
        answer = await self.lead(WORKFLOW_FOLLOWUP)
        assert_workflow_judgment(self.history(), answer)
        require(answer.kind is LeadOutcomeKind.ANSWER and answer.proposal is None,
                'Fresh post-budget Lead answer was not observed')
        require(self.history()[:len(history)] == history,
                'Fresh input rewrote the completed budget history')
        require(0 < len(self.observer.calls) - before <= 6,
                'Fresh post-budget context exceeded its declared bound')
        save(self.directory / 'budget-finalization.sanitized.json', {
            'classification': 'live_fault_injection', 'lineage_calls': before,
            'followup_calls': len(self.observer.calls) - before,
            'terminal_history_sha256': semantic_digest(canonical_history(history)),
            'next_intent_retained': True, 'fresh_authority_created': False})

    async def dispatch(self):
        id = self.spec['id']
        if id in AGENTS_BY_CASE:
            await self.normal_consultation()
        elif id == 'L06C-RECOMMENDATION':
            text = self.spec['instruction'] + '\nFinished user-supplied outline:\n' + self.spec['candidate_text']
            result = await self.lead(text)
            assert_pending_plot_recommendation(self.history(), result, self.observer.calls)
        elif id in {'L06A-QUESTION', 'L06B-CRITIQUE'}:
            text = self.spec['instruction']
            if id == 'L06B-CRITIQUE':
                text += ' ${core_documents/global_outline.md} ${manuscript/part-01/opening.md}#L2-L4'
            result = await self.lead(text)
            expected = {'L06A-QUESTION': LeadOutcomeKind.QUESTION, 'L06B-CRITIQUE': LeadOutcomeKind.CRITIQUE}[id]
            require(result.kind is expected, 'Required natural Lead outcome absent')
            assert_workflow_judgment(self.history(), result)
            require(result.proposal is None, 'Unexpected specialist proposal')
            if id == 'L06B-CRITIQUE':
                judgments = [e.payload for e in self.history()
                             if isinstance(e.payload, CollaborationLeadJudgmentPayload)]
                receipts = judgments[-1].evidence.receipts
                require(any(r.locator == '${core_documents/global_outline.md}'
                            and r.boundary_kind == 'whole'
                            and r.start_line is None and r.end_line is None for r in receipts),
                        'Whole-file outline read provenance absent')
                require(any(r.locator == '${manuscript/part-01/opening.md}#L2-L4'
                            and r.start_line == 2 and r.end_line == 4 for r in receipts),
                        'Ranged draft read provenance absent')
                first = self.observer.calls[0]
                command = json.loads((Path(first['directory']) / 'command.private.json').read_text())['argv']
                payload = json.loads(command[command.index('--print') + 1])
                require(payload.get('operation') == 'writing_artifact_reference_read'
                        and first.get('action') == 'read_writing_artifacts', 'Mandatory READ prelude absent')
        elif id == 'L07-SEARCH-READ':
            result = await self.lead(self.spec['instruction'])
            require(result.kind is LeadOutcomeKind.ANSWER and 'day 4' in result.message, 'Retrieved fact absent')
            assert_workflow_judgment(self.history(), result)
            exchanges = [json.loads(line)
                         for path in self.workspace.path('debug_logs/agent_exchanges/live').glob('*.jsonl')
                         for line in path.read_text().splitlines()]
            audit = audit_exchanges(exchanges, self.observer.calls, self.history())
            accepted = {row['number'] for row in audit if row['outcome'] == 'accepted'}
            require([c.get('action') for c in self.observer.calls if c['number'] in accepted]
                    == ['search', 'read', 'answer'], 'Actual accepted SEARCH/READ/ANSWER sequence absent')
        elif id == 'L08-DECISIONS':
            await self.decisions()
        elif id == 'L09-SECOND-ROUND':
            await self.second_round()
        elif id == 'F01-REFERENCE-DRIFT':
            await self.reference_drift()
        elif id == 'F02-LOST-AUTHORITY':
            await self.lost_authority_child()
        elif id == 'F03-CANCELLATION':
            await self.interrupted_or_failed(cancel=True)
        elif id == 'F04-FAILURE-RECOVERY':
            await self.interrupted_or_failed(cancel=False)
        elif id == 'F05-DIAGNOSTICS-RECOVERY':
            await self.diagnostic_cases()
        elif id == 'F06-BUDGET-FINALIZATION':
            await self.budget_finalization()
        else:
            raise AssertionError('Unknown authorized live case')

class LiveTerminal:
    """A real controlling terminal, with retained bytes and normal cleanup."""

    def __init__(self, case: ConversationCase, *, label: str):
        import fcntl
        import pty
        import struct
        import termios
        self.case = case
        self.label = label
        self.raw = bytearray()
        self.auth_rechecks = 0
        self.at_auth_picker = False
        self.at_picker = False
        self.in_viewer = False
        self.master, slave = pty.openpty()
        self.termios = termios
        self.original = termios.tcgetattr(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 200, 0, 0))

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            os.tcsetpgrp(0, os.getpgrp())
        env = dict(os.environ, TERM='dumb', NO_COLOR='1', STORY_REDUCED_MOTION='1')
        argv = [sys.executable, '-B', '-m', 'tests.antigravity_live_harness', '--shell-child', case.spec['id'], '--evidence', str(case.directory.parent)]
        self.process = subprocess.Popen(argv, cwd=Path(__file__).resolve().parents[1], env=env, stdin=slave, stdout=slave, stderr=slave, preexec_fn=controlling_terminal, close_fds=True)
        os.close(slave)

    def text(self):
        import re
        return re.sub('\\x1b\\[[0-?]*[ -/]*[@-~]', '', self.raw.decode('utf-8', errors='replace')).replace('\r', '')

    def send(self, value):
        os.write(self.master, value if isinstance(value, bytes) else (value + '\n').encode())

    async def wait(self, needles, *, after=0, seconds=180):
        import errno
        import select
        if isinstance(needles, str):
            needles = (needles,)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            text = self.text()[after:]
            require('story[lobby]> ' not in text, 'Shell did not attach the prepared session')
            if 'Provider authentication > ' in text:
                self.at_auth_picker = True
                require('Antigravity — Status indeterminate. Select to check again.' in text
                        and self.auth_rechecks < 2,
                        'Native status requires login or exceeded bounded read-only rechecks')
                self.auth_rechecks += 1
                after = len(self.text())
                self.send('')
                await asyncio.sleep(0.05)
                continue
            for needle in needles:
                if needle in text:
                    if needle == 'story[live]> ':
                        self.at_auth_picker = False
                        self.at_picker = False
                        self.in_viewer = False
                    elif needle.endswith(' > '):
                        self.at_picker = True
                    return needle
            require(self.process.poll() is None, 'Shell exited before required visible state')
            if select.select([self.master], [], [], 0)[0]:
                try:
                    self.raw.extend(os.read(self.master, 65536))
                    (self.case.directory / (self.label + '.pty.in-progress.private.txt')).write_text(self.text())
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
            await asyncio.sleep(0.05)
        raise TimeoutError('Required shell-visible state was not reached')

    async def close(self):
        import errno
        import select
        import signal

        cursor = len(self.raw)
        quit_sent = not (self.at_auth_picker or self.at_picker or self.in_viewer)
        if self.process.poll() is None:
            self.send('/quit' if quit_sent else ('q' if self.in_viewer else '/cancel'))
            deadline = time.monotonic() + 10
            while self.process.poll() is None and time.monotonic() < deadline:
                if select.select([self.master], [], [], 0)[0]:
                    try:
                        self.raw.extend(os.read(self.master, 65536))
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                if not quit_sent and b'story[live]> ' in self.raw[cursor:]:
                    self.send('/quit')
                    quit_sent = True
                await asyncio.sleep(0.05)
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(self.process.wait, 5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    await asyncio.to_thread(self.process.wait, 5)
        (self.case.directory / (self.label + '.pty.raw')).write_bytes(self.raw)
        (self.case.directory / (self.label + '.pty.private.txt')).write_text(self.text())
        restored = self.termios.tcgetattr(self.master) == self.original
        os.close(self.master)
        save(self.case.directory / (self.label + '.pty.sanitized.json'), {
            'exit_code': self.process.returncode,
            'terminal_restored': restored,
            'read_only_auth_rechecks': self.auth_rechecks,
        })
        require(self.process.returncode == 0 and restored, 'PTY exit or terminal restoration failed')


async def close_terminal(terminal, primary_error=None):
    """Join the owned terminal without replacing the original case failure."""
    try:
        await terminal.close()
    except BaseException as cleanup_error:
        save(terminal.case.directory / (terminal.label + '.cleanup-error.sanitized.json'), {
            'primary_error_type': type(primary_error).__name__ if primary_error else None,
            'cleanup_error_type': type(cleanup_error).__name__,
        })
        if primary_error is None:
            raise
        primary_error.add_note('Owned terminal cleanup also failed; separate evidence retained.')


def assert_terminal_focus(case, text):
    from story_writing_agents.cli_context import load_default_conversation
    require('command_invalid' not in text and 'conversation_required' not in text,
            'Shell setup command failed before prose submission')
    require(load_default_conversation(case.workspace, case.session) == case.conversation,
            'Shell durable focus differs from the exact intended conversation')


async def pty_consultation(case):
    case.backend.close()
    case.backend = None
    terminal = LiveTerminal(case, label='fresh')
    try:
        await terminal.wait('story[live]> ', seconds=60)
        cursor = len(terminal.text())
        terminal.send('/conversation resume ' + case.conversation)
        await terminal.wait('story[live]> ', after=cursor)
        assert_terminal_focus(case, terminal.text()[cursor:])
        cursor = len(terminal.text())
        terminal.send(consultation_request(case.spec, AGENTS_BY_CASE[case.spec['id']]))
        reached = await terminal.wait(('Lead proposal decision > ', 'story[live]> '), after=cursor, seconds=stage_seconds(7))
        require(reached == 'Lead proposal decision > ', 'Required actual shell proposal absent')
        projection = project_collaboration_history(case.history())
        proposed_decision = LeadStepDecision(
            confirmation_request_id=projection.confirmation_request_id,
            session_id=case.session, conversation_id=case.conversation,
            step_id=projection.current_step_id,
            proposal_id=projection.current_proposal.proposal_id,
            proposal_revision=projection.current_proposal.proposal_revision,
            proposal_digest=projection.current_proposal.proposal_digest,
            kind=CollaborationConfirmationDecision.CONFIRM, revision_request=None)
        assert_review_proposal(projection.current_proposal, AGENTS_BY_CASE[case.spec['id']])
        require(not any((c.get('agent') != 'lead_orchestrator' for c in read_call_records(case.directory))), 'Shell dispatched before confirmation')
        cursor = len(terminal.text())
        terminal.send('1')
        await terminal.wait('story[live]> ', after=cursor, seconds=stage_seconds(12))
        case.audit_completed_history(AGENTS_BY_CASE[case.spec['id']])
        cursor = len(terminal.text())
        terminal.send('/history')
        await terminal.wait('story[live]> ', after=cursor)
        history_text = terminal.text()[cursor:]
        replies = [e.payload for e in case.history() if isinstance(e.payload, CollaborationSpecialistReplyPayload)]
        require(len(replies) == 2, 'Shell missing two actual specialist replies')
        require('plot_architect' in history_text and 'character_custodian' in history_text, 'Shell history lacks specialist attribution')
        assert_visible_messages(history_text, case.history())
        cursor = len(terminal.text())
        terminal.send('/exchanges')
        await terminal.wait('View agent exchanges > ', after=cursor)
        cursor = len(terminal.text())
        terminal.send('1')
        await terminal.wait('Choose a canonical agent.', after=cursor)
        await terminal.wait('View agent exchanges > ', after=cursor)
        cursor = len(terminal.text())
        terminal.send('1')
        await terminal.wait(('Search', 'search', 'Rows', 'records', 'request'), after=cursor, seconds=30)
        terminal.in_viewer = True
        terminal.send('q')
        await terminal.wait('story[live]> ', after=cursor, seconds=30)
        require('viewer failed' not in terminal.text()[cursor:].lower(), 'Exchange viewer failed')
    finally:
        await close_terminal(terminal, sys.exc_info()[1])
    prior_calls = read_call_records(case.directory)
    prior_lead = [c for c in prior_calls if c['agent'] == 'lead_orchestrator'][-1]
    reopened = LiveTerminal(case, label='reopened')
    try:
        await reopened.wait('story[live]> ', seconds=60)
        assert_terminal_focus(case, reopened.text())
        cursor = len(reopened.text())
        reopened.send(WORKFLOW_FOLLOWUP)
        await reopened.wait('story[live]> ', after=cursor, seconds=stage_seconds(6))
        require('Outcome: answer' in reopened.text()[cursor:] and MARKER in reopened.text()[cursor:], 'Reopened shell did not answer/recall')
        followup_text = reopened.text()[cursor:]
        followup_history = case.history()
        from types import SimpleNamespace
        assert_workflow_judgment(followup_history, SimpleNamespace(kind=LeadOutcomeKind.ANSWER))
        latest = [e.payload for e in followup_history
                  if isinstance(e.payload, CollaborationLeadJudgmentPayload)][-1]
        require(normalized_visible(latest.message) in normalized_visible(followup_text),
                'Shell follow-up differs from persisted answer')
        resumed = read_call_records(case.directory)[len(prior_calls):]
        require(resumed and resumed[0]['resumed']
                and resumed[0]['prior_thread_id'] == prior_lead['thread_id'],
                'Shell reopen failed to resume the original native thread')
        all_calls = read_call_records(case.directory)
        assert_resumed_call_chain(resumed, prior_lead['thread_id'], audit_case_exchanges(case, all_calls))
        calls_before_switch = all_calls
        cursor = len(reopened.text())
        reopened.send('/conversation new Other conversation')
        await reopened.wait('story[live]> ', after=cursor)
        cursor = len(reopened.text())
        reopened.send('/conversation resume ' + case.conversation)
        await reopened.wait('story[live]> ', after=cursor)
        assert_terminal_focus(case, reopened.text()[cursor:])
        require(case.history() == followup_history, 'Conversation switch changed original history/state')
        require(read_call_records(case.directory) == calls_before_switch, 'Conversation switch replayed a provider call')
    finally:
        await close_terminal(reopened, sys.exc_info()[1])
    case.observer.calls = read_call_records(case.directory)
    case.open_backend()
    history = case.history()
    latest = [e.payload for e in history if isinstance(e.payload, CollaborationLeadJudgmentPayload)][-1]
    require(latest.kind is LeadOutcomeKind.ANSWER and MARKER in latest.message, 'Persisted resumed answer differs')
    await case.rejects_decision(proposed_decision)
    case.turns = []
    return {'lead_outcomes': ['proposal', 'answer'], 'pty_confirm_history_exchanges_resume': True}

def read_call_records(directory):
    return [json.loads(path.read_text()) for path in sorted((directory / 'calls').glob('call-*/summary.sanitized.json'))]

def load_inventory():
    return json.loads((Path(__file__).parent / 'fixtures/antigravity_live_cases.json').read_text())

def selected_spec(case_id):
    matches = [spec for spec in load_inventory()['cases'] if spec['id'] == case_id]
    require(len(matches) == 1, 'Unknown case selection')
    return matches[0]

def require_opt_in():
    require(os.environ.get('STORY_RUN_LIVE') == '1', 'Set STORY_RUN_LIVE=1 explicitly to invoke real providers')
    require(shutil.which(os.environ.get('STORY_ANTIGRAVITY_EXECUTABLE', 'agy')) is not None, 'Antigravity executable unavailable')

async def wait_owned_fault_child(child, timeout):
    """Join an isolated F02 child on success, timeout and cancellation."""
    import signal

    def stop(hard):
        if child.returncode is not None:
            return
        try:
            if os.name == 'posix':
                os.killpg(child.pid, signal.SIGKILL if hard else signal.SIGTERM)
            elif hard:
                child.kill()
            else:
                child.terminate()
        except ProcessLookupError:
            pass

    try:
        return await asyncio.wait_for(child.wait(), timeout)
    except BaseException:
        stop(False)
        try:
            await asyncio.wait_for(asyncio.shield(child.wait()), 5)
        except asyncio.TimeoutError:
            stop(True)
            await child.wait()
        raise


async def run_case(case_id, evidence_root):
    require_opt_in()
    spec = selected_spec(case_id)
    evidence_root = Path(evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    directory = evidence_root / case_id
    directory.mkdir(mode=448)
    started = time.monotonic()
    summary = {'case': case_id, 'classification': spec['classification'], 'passed': False}
    case = None
    try:
        if case_id == 'L05-MIXED':
            require(shutil.which(os.environ.get('STORY_CODEX_EXECUTABLE', 'codex')) is not None, 'Codex executable unavailable')
        if case_id == 'F02-LOST-AUTHORITY':
            argv = [sys.executable, '-B', '-m', 'tests.antigravity_live_harness', '--fault-child', case_id, '--evidence', str(evidence_root)]
            with (directory / 'fault-child.private.log').open('wb') as log:
                child = await asyncio.create_subprocess_exec(
                    *argv, stdout=log, stderr=log, start_new_session=True)
                code = await wait_owned_fault_child(child, stage_seconds(6))
            require(code == 86, 'Expected durable-confirmation crash checkpoint absent')
            case = ConversationCase(spec, directory, evidence_root)
            case.observer.calls = read_call_records(directory)
            await case.lost_authority_resume()
        else:
            case = ConversationCase(spec, directory, evidence_root)
            case.setup()
            if case_id == 'L01-PRO-HIGH-PTY':
                summary.update(await pty_consultation(case))
            else:
                await case.dispatch()
        final = case.final_checks()
        if 'lead_outcomes' in summary:
            final['lead_outcomes'] = sorted(set(final['lead_outcomes']) | set(summary['lead_outcomes']))
        summary.update(final, passed=True)
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__, error=str(exc))
        save(directory / 'failure.private.json', summary)
        summary.pop('error', None)
    finally:
        if case is not None:
            try:
                case.close()
            except BaseException as exc:
                summary.update(passed=False, cleanup_error_type=type(exc).__name__)
        summary['seconds'] = time.monotonic() - started
        save(directory / 'result.sanitized.json', summary)
    print(json.dumps(summary, default=str), flush=True)
    return summary

async def run_selected(case_id, evidence_root):
    ids = [spec['id'] for spec in load_inventory()['cases']] if case_id == 'all' else [case_id]
    summaries = []
    for id in ids:
        result = await run_case(id, evidence_root)
        summaries.append(result)
        if not result['passed']:
            break
    completed = {s['case'] for s in summaries}
    remaining = [id for id in ids if id not in completed]
    passed = not remaining and all((s['passed'] for s in summaries))
    if case_id == 'all' and passed:
        kinds = {k for s in summaries for k in s.get('lead_outcomes', [])}
        assessments = {k for s in summaries for k in s.get('assessments', [])}
        passed = kinds >= {'answer', 'question', 'critique', 'recommendation', 'proposal'} and assessments >= {'supports', 'concern', 'conflict', 'insufficient_evidence'}
    save(Path(evidence_root) / 'matrix-result.sanitized.json', {'passed': passed, 'cases': summaries, 'unexecuted': remaining})
    return 0 if passed else 1

async def setup_smoke(evidence_root):
    os.environ['STORY_LIVE_SETUP_ONLY'] = '1'
    started = time.monotonic()
    spec = selected_spec('L01-PRO-HIGH-PTY')
    evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = evidence_root / spec['id']
    directory.mkdir(mode=0o700)
    case = ConversationCase(spec, directory, evidence_root)
    terminal = None
    result = {'passed': False, 'model_calls': 0}
    try:
        case.setup()
        require(all(case.backend._antigravity_adapter.invocation_settings(agent)[0]
                    == 'gemini-3.1-pro-high' for agent in ROSTER),
                'Pro PTY fixture model settings differ')
        case.backend.close()
        case.backend = None
        terminal = LiveTerminal(case, label='setup-only')
        await terminal.wait('story[live]> ', seconds=100)
        await terminal.close()
        terminal = None
        require(not read_call_records(directory), 'Setup submitted a model call')
        require(git_snapshot(case.workspace.root) == case.git_baseline,
                'Setup shell changed Git HEAD or index')
        result['passed'] = True
    except BaseException as exc:
        import traceback
        (evidence_root / 'setup-failure.private.txt').write_text(traceback.format_exc())
        result.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if terminal is not None:
            try:
                await terminal.close()
            except BaseException as exc:
                result['cleanup_error_type'] = type(exc).__name__
        case.close()
    result['seconds'] = time.monotonic() - started
    save(evidence_root / 'setup-result.sanitized.json', result)
    print(json.dumps(result), flush=True)
    return 0 if result['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='all')
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--shell-child')
    parser.add_argument('--fault-child')
    parser.add_argument('--setup-smoke', action='store_true')
    args = parser.parse_args(argv)
    require_opt_in()
    os.umask(63)
    if args.setup_smoke:
        return asyncio.run(setup_smoke(args.evidence.resolve()))
    if args.shell_child:
        spec = selected_spec(args.shell_child)
        directory = args.evidence.resolve() / spec['id']
        observer = CallEvidence(directory, spec, args.evidence.resolve())
        observer.calls = read_call_records(directory)
        observer.install()
        from story_writing_agents.cli import main as story_main
        paths = json.loads((directory / 'workspace.private.json').read_text())
        command = ['chat', 'live', '--workspace', paths['session'], '--antigravity-executable', os.environ.get('STORY_ANTIGRAVITY_EXECUTABLE', 'agy'), '--antigravity-model', MODEL_BY_CASE[spec['id']], '--codex-timeout', '180']
        for agent in ROSTER:
            command.extend(['--backend', agent + '=antigravity'])
        try:
            return story_main(command)
        finally:
            observer.close()
    if args.fault_child:
        case = ConversationCase(selected_spec(args.fault_child), args.evidence.resolve() / args.fault_child, args.evidence.resolve())
        case.setup()
        asyncio.run(case.lost_authority_child())
        return 1
    return asyncio.run(run_selected(args.case, args.evidence.resolve()))
if __name__ == '__main__':
    raise SystemExit(main())
