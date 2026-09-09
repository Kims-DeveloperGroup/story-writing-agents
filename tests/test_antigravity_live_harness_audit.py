"""Offline contracts for live evidence acceptance; no provider execution."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import antigravity_live_harness as harness


def scoped_proposal(agents=('plot_architect',), *, mode='review', proposal_id='scoped_proposal'):
    from dataclasses import fields, replace
    from story_writing_agents import conversations as contracts
    from story_writing_agents.specialist_routing import (ArtifactReviewDomain, AuthoringActivity,
        AuthoringScope, SpecialistAssignmentMode, ReviewBasis)
    from tests import collaboration_contract_fixture as fixture
    domains = dict(zip(harness.BUDGET_REVIEWERS, ArtifactReviewDomain))
    assignment_mode = SpecialistAssignmentMode(mode)
    inputs = (fixture.make_input('candidate', kind=contracts.CollaborationInputKind.USER_INPUT,
        locator='Finished candidate: Nara arrives on day 1 and observes the door on day 3.',
        start_line=None, end_line=None),)
    assignments = tuple(replace(fixture.make_assignment('assignment_' + agent, specialist_id=agent,
        input_ids=('candidate',)), mode=assignment_mode,
        review_basis=ReviewBasis(('candidate',), 'Assess the supplied facts without inventing missing evidence.')
        if mode == 'review' else None) for agent in agents)
    # Build the legacy scaffold, then create one digest-bound current proposal.
    legacy = fixture.make_proposal(proposal_id=proposal_id, inputs=inputs,
        assignments=tuple(replace(a, mode=None, review_basis=None) for a in assignments))
    values = {field.name: getattr(legacy, field.name) for field in fields(legacy)
              if field.name not in {'proposal_digest', 'authoring_scope', 'assignments'}}
    return contracts.CollaborationProposal.create(**values, assignments=assignments,
        authoring_scope=AuthoringScope(AuthoringActivity(mode), (), tuple(domains[a] for a in agents),
            'Finished supplied candidate.' if mode == 'review' else 'Requested draft.', proposal_id, 1))


def scoped_executed_history(proposal, *, failed_assignment_id=None):
    from story_writing_agents import conversations as contracts
    from tests import collaboration_contract_fixture as fixture
    by_id = {a.assignment_id: a for a in proposal.assignments}
    def reply(*args, **kwargs):
        assignment = by_id[args[4]]
        if assignment.mode is harness.SpecialistAssignmentMode.DRAFT:
            args = (*args[:6], contracts.CollaborationSpecialistAssessment.DRAFTED, *args[7:])
        return contracts.CollaborationSpecialistReplyPayload(*args, **kwargs, mode=assignment.mode)
    def synthesis(*args, **kwargs):
        return contracts.CollaborationLeadSynthesisPayload(*args, **kwargs, authoring_scope=proposal.authoring_scope)
    def judgment(*args, **kwargs):
        return contracts.CollaborationLeadJudgmentPayload(*args, **kwargs, authoring_scope=proposal.authoring_scope)
    with mock.patch.object(fixture, 'CollaborationSpecialistReplyPayload', side_effect=reply), \
         mock.patch.object(fixture, 'CollaborationLeadSynthesisPayload', side_effect=synthesis), \
         mock.patch.object(fixture, 'CollaborationLeadJudgmentPayload', side_effect=judgment):
        return fixture.executed_history(proposal, failed_assignment_id=failed_assignment_id)


class LiveHarnessAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = dict(session_id="session", conversation_id="conversation",
                             turn_id="turn", step_id="step", agent_id="lead_orchestrator")
        current = dict(identity=self.identity, workflow="lead_front_channel", command="Review once")
        prelude = lambda ordinal: dict(operation="writing_artifact_reference_read", correlation={
            "operation_id": "reference-operation", "invocation_ordinal": ordinal,
            "agent_id": "lead_orchestrator", "phase": "lead"})
        correction = dict(current, response_correction={
            "rejection_code": "answer_validation_failed", "instruction": "Return ANSWER now"})
        self.payloads = [prelude(1), dict(c="correlation", q=["lead", "sa", "wcdo"], i=current,
                                       untrusted_writing_artifact_context=["reference"]),
                         prelude(2), dict(c="correlation", q=["lead", "a", ""], i=correction,
                                          untrusted_writing_artifact_context=["reference"])]
        self.calls = []
        self.exchanges = []
        self.history = []
        for index, payload in enumerate(self.payloads, 1):
            directory = self.root / str(index)
            directory.mkdir()
            thread = "old-thread" if index <= 2 else "fresh-thread"
            prior = thread if index in (2, 4) else None
            schema = {"type": "object"}
            content = {"correlation": "correlation", "action": {"kind": "answer", "result": {
                "kind": "answer", "message": "Correct answer" if index == 4 else "Rejected answer"}}}
            native = dict(status="SUCCESS", conversation_id=thread,
                          json_schema=schema, structured_output=content)
            argv = ["unused-agy", "--json-schema", harness.canonical_provider_json(schema).decode(), "--print", json.dumps(payload)]
            (directory / "command.private.json").write_text(json.dumps({"argv": argv}))
            raw = harness.canonical_provider_json(native)
            for name in ("stdout.raw", "delivered.stdout"):
                (directory / name).write_bytes(raw)
            for name in ("stderr.raw", "delivered.stderr", "stdin.bin"):
                (directory / name).write_bytes(b"")
            call = dict(number=index, agent="lead_orchestrator", vendor="antigravity",
                        directory=str(directory), fault=None, delivered_returncode=0,
                        native_status="SUCCESS", thread_id=thread, prior_thread_id=prior,
                        payload_bytes=len(argv[-1].encode()), payload_sha256=harness.digest(argv[-1].encode()),
                        payload_semantic_sha256=harness.semantic_digest(payload),
                        schema_semantic_sha256=harness.semantic_digest(schema))
            self.calls.append(call)
            outcome = {"outcome": "rejected", "code": "answer_validation_failed"} if index == 2 else {
                "outcome": "accepted", "code": "accepted"}
            values = [dict(payload=payload, schema=schema, prior_thread_id=prior),
                      dict(response_text=json.dumps(content), thread_id=thread,
                           actual_transport_utf8_bytes=call["payload_bytes"]), outcome]
            for kind, value in zip(("request", "response", "outcome"), values):
                self.exchanges.append(dict(exchange_id=str(index), agent="lead_orchestrator",
                                           agent_id="lead_orchestrator", request_type=kind,
                                           content=json.dumps(value)))
            for state in ("prepared", "submitted"):
                self.history.append(dict(self.identity, sequence=len(self.history) + 1, payload=dict(
                    payload_type="provider_call", state=state, role="lead_orchestrator",
                    invocation_kind="lead", invocation_id="logical-invocation", ordinal=index,
                    call_id="physical-" + str(index),
                    call_kind="reference_prelude" if index in (1, 3) else "initial")))
        self.history.append(dict(self.identity, sequence=9, payload=dict(
            payload_type="lead_judgment", kind="answer", message="Correct answer")))

    def audit(self):
        return harness.audit_exchanges(self.exchanges, self.calls, self.history)

    def test_exact_validation_correction_remains_rejected_then_accepted(self):
        result = self.audit()
        self.assertEqual([r["outcome"] for r in result], ["accepted", "rejected", "accepted", "accepted"])
        self.assertEqual(result[1]["correction_proof"]["physical_calls"], 4)
        self.assertEqual(result[1]["correction_proof"]["corrected_call"], 4)

    def test_duplicate_requests_require_unique_native_response_identity(self):
        calls = []
        groups = []
        payload = {"question": "One synthetic diagnostic answer"}
        schema = {"type": "object"}
        for index in range(6):
            directory = self.root / f"duplicate-{index}"
            directory.mkdir()
            thread = f"native-{index}"
            content = {"action": {"kind": "answer", "result": f"answer-{index}"}}
            native = {"status": "SUCCESS", "conversation_id": thread,
                      "json_schema": schema, "structured_output": content}
            raw = harness.canonical_provider_json(native)
            failed = index >= 3
            delivered = f"malformed-{index}".encode() if failed else raw
            argv = ["unused-agy", "--json-schema", harness.canonical_provider_json(schema).decode(),
                    "--print", json.dumps(payload)]
            (directory / "command.private.json").write_text(json.dumps({"argv": argv}))
            for name, value in (("stdout.raw", raw), ("delivered.stdout", delivered),
                                ("stderr.raw", b""), ("delivered.stderr", b"")):
                (directory / name).write_bytes(value)
            call = dict(number=index + 1, agent="plot_architect", vendor="antigravity",
                        directory=str(directory), fault="json" if failed else None,
                        delivered_returncode=0, native_status="SUCCESS", thread_id=thread,
                        prior_thread_id=None, payload_bytes=len(argv[-1].encode()),
                        payload_sha256=harness.digest(argv[-1].encode()),
                        payload_semantic_sha256=harness.semantic_digest(payload),
                        schema_semantic_sha256=harness.semantic_digest(schema))
            calls.append(call)
            response = {"response_text": json.dumps(content), "thread_id": thread,
                        "actual_transport_utf8_bytes": call["payload_bytes"]}
            if failed:
                response = {"provider_failure": True, "response_diagnostic": {
                    "exit_code": 0, "stdout": {"encoding": "utf-8", "data": delivered.decode()},
                    "stderr": {"encoding": "utf-8", "data": ""}}}
            values = [{"payload": payload, "schema": schema, "prior_thread_id": None}, response,
                      {"outcome": "provider_failure" if failed else "accepted",
                       "code": "provider_invocation_failed" if failed else "accepted"}]
            groups.append([dict(exchange_id=f"exchange-{index}", agent_id="plot_architect",
                                request_type=kind, content=json.dumps(value))
                           for kind, value in zip(("request", "response", "outcome"), values)])

        def audit(selected_groups, selected_calls=calls):
            return harness.audit_exchanges(
                [record for group in reversed(selected_groups) for record in group], selected_calls)

        self.assertEqual([row["outcome"] for row in audit(groups)],
                         ["accepted"] * 3 + ["provider_failure"] * 3)
        for variant in ("wrong_thread", "wrong_diagnostic", "duplicate", "missing_response",
                        "unavailable_output"):
            changed = copy.deepcopy(groups)
            selected_calls = copy.deepcopy(calls)
            if variant == "wrong_thread":
                response = json.loads(changed[0][1]["content"])
                response["thread_id"] = "wrong-native"
                changed[0][1]["content"] = json.dumps(response)
            elif variant == "wrong_diagnostic":
                response = json.loads(changed[3][1]["content"])
                response["response_diagnostic"]["stdout"]["data"] = "wrong-bytes"
                changed[3][1]["content"] = json.dumps(response)
            elif variant == "duplicate":
                duplicate = copy.deepcopy(changed[0])
                for record in duplicate:
                    record["exchange_id"] = "indistinguishable"
                changed.append(duplicate)
            elif variant == "missing_response":
                changed[0].pop(1)
            else:
                selected_calls[0].pop("delivered_returncode")
                selected_calls[0]["exception_type"] = "CancelledError"
            with self.subTest(variant=variant), self.assertRaisesRegex(
                    AssertionError, "unambiguous durable REQUEST"):
                audit(changed, selected_calls)

    def test_unavailable_output_distinguishes_failure_response_from_cancellation(self):
        call = copy.deepcopy(self.calls[0])
        call.pop("delivered_returncode")
        call["exception_type"] = "ProviderInvocationError"
        request, response, terminal = copy.deepcopy(self.exchanges[:3])
        response["content"] = json.dumps({"provider_failure": True})
        terminal["content"] = json.dumps({"code": "provider_timed_out", "outcome": "provider_failure"})
        failure_records = [request, response, terminal]
        failure_audit = harness.audit_exchanges(failure_records, [call])
        self.assertEqual(failure_audit[0]["outcome"], "provider_failure")
        self.assertIs(failure_audit[0]["completed_output"], False)

        cancelled_call = dict(call, exception_type="CancelledError")
        cancelled_terminal = dict(terminal, content=json.dumps({"code": "cancelled", "outcome": "cancelled"}))
        cancelled_records = [request, cancelled_terminal]
        cancelled_audit = harness.audit_exchanges(cancelled_records, [cancelled_call])
        self.assertEqual(cancelled_audit[0]["outcome"], "cancelled")
        self.assertIs(cancelled_audit[0]["completed_output"], False)

        for variant in ("missing_failure_response", "duplicate_failure_response", "extra_cancelled_response",
                        "accepted_shape", "unverified_diagnostic", "nonboolean_marker", "missing_exception"):
            records = copy.deepcopy(failure_records)
            selected_call = dict(call)
            if variant == "missing_failure_response":
                records.pop(1)
            elif variant == "duplicate_failure_response":
                records.insert(1, copy.deepcopy(response))
            elif variant == "extra_cancelled_response":
                records = [request, response, cancelled_terminal]
                selected_call = cancelled_call
            elif variant == "accepted_shape":
                records[1]["content"] = self.exchanges[1]["content"]
            elif variant == "unverified_diagnostic":
                records[1]["content"] = json.dumps({"provider_failure": True, "response_diagnostic": {
                    "stdout": {"encoding": "utf-8", "data": "unverified"},
                    "stderr": {"encoding": "utf-8", "data": ""}, "exit_code": 1}})
            elif variant == "nonboolean_marker":
                records[1]["content"] = json.dumps({"provider_failure": 1})
            else:
                selected_call.pop("exception_type")
            with self.subTest(variant=variant), self.assertRaisesRegex(
                    AssertionError, "RESPONSE count|exact synthetic marker|accepted or unexplained"):
                harness.audit_exchanges(records, [selected_call])

    def test_typed_events_preserve_discriminators_through_complete_audit(self):
        from story_writing_agents import conversations as contracts
        from tests.collaboration_contract_fixture import LEAD, RUNTIME, USER, make_lead_evidence

        typed = []
        for record in self.history:
            value = record["payload"]
            provider = value["payload_type"] == "provider_call"
            if provider:
                payload = contracts.CollaborationProviderCallPayload(
                    value["invocation_id"], value["call_id"], value["ordinal"], value["role"],
                    contracts.CollaborationInvocationKind(value["invocation_kind"]),
                    contracts.CollaborationProviderCallKind(value["call_kind"]),
                    contracts.CollaborationProviderCallState(value["state"]),
                )
            else:
                payload = contracts.CollaborationLeadJudgmentPayload(
                    contracts.LeadOutcomeKind.ANSWER, value["message"], make_lead_evidence(),
                )
            typed.append(contracts.CollaborationEvent(
                schema_version=contracts.COLLABORATION_EVENT_SCHEMA_VERSION,
                event_id="event_" + str(record["sequence"]),
                session_id=record["session_id"], conversation_id=record["conversation_id"],
                turn_id=record["turn_id"], step_id=record["step_id"],
                sequence=record["sequence"], timestamp="2026-09-01T00:00:00+00:00",
                batch_id="batch_" + str(record["sequence"]), batch_ordinal=0,
                batch_size=1, batch_digest="sha256:" + "a" * 64,
                kind=(contracts.CollaborationEventKind.PROVIDER_CALL_ACCOUNTING if provider
                      else contracts.CollaborationEventKind.MESSAGE),
                sender=RUNTIME if provider else LEAD, recipient=LEAD if provider else USER,
                payload=payload,
            ))
        canonical = [json.loads(contracts.encode_collaboration_event(event)) for event in typed]
        self.assertEqual(harness.canonical_history(typed), canonical)
        self.assertIs(harness.canonical_history([canonical[0]])[0], canonical[0])
        self.assertEqual(harness.audit_exchanges(self.exchanges, self.calls, typed), self.audit())


    def test_current_scenario_review_bases_and_exact_domains(self):
        from types import SimpleNamespace
        from dataclasses import replace, fields
        from story_writing_agents import conversations as contracts
        from story_writing_agents.specialist_routing import artifact_domain, specialists_for_domains
        selections = {**harness.AGENTS_BY_CASE, 'L08-DECISIONS': ('plot_architect',),
            'L09-SECOND-ROUND': ('plot_architect',), 'F01-REFERENCE-DRIFT': ('plot_architect',),
            'F02-LOST-AUTHORITY': ('plot_architect',),
            'F03-CANCELLATION': ('plot_architect', 'character_custodian'),
            'F04-FAILURE-RECOVERY': ('plot_architect', 'character_custodian'),
            'F06-BUDGET-FINALIZATION': harness.BUDGET_REVIEWERS}
        no_references = {'L08-DECISIONS', 'L09-SECOND-ROUND', 'F02-LOST-AUTHORITY',
                         'F03-CANCELLATION', 'F04-FAILURE-RECOVERY'}
        for case_id, agents in selections.items():
            with self.subTest(case=case_id):
                spec = harness.selected_spec(case_id)
                proposal = scoped_proposal(agents)
                turn = SimpleNamespace(kind=harness.LeadOutcomeKind.PROPOSAL,
                    state=harness.CollaborationStepState.AWAITING_CONFIRMATION, proposal=proposal)
                case = SimpleNamespace(spec=spec, observer=SimpleNamespace(calls=[]),
                    lead=mock.AsyncMock(return_value=turn))
                self.assertIs(asyncio.run(harness.ConversationCase.propose(
                    case, agents, references=case_id not in no_references)), turn)
                request = case.lead.await_args.args[0]
                paths = re.findall(r'\$\{([^}]+)\}', request)
                domains = tuple(dict.fromkeys(artifact_domain(path) for path in paths))
                self.assertTrue(set(specialists_for_domains(domains)) <= set(agents))
                if case_id in no_references or case_id == 'L02-PRO-LOW-ONE':
                    self.assertIn(spec['candidate_text'], request)
                if case_id == 'L02-PRO-LOW-ONE':
                    self.assertNotIn('${manuscript/', request)
                missing = replace(proposal.basis.inputs[0], state=contracts.CollaborationInputState.ABSENT)
                values = {field.name: getattr(proposal, field.name) for field in fields(proposal)
                          if field.name not in {'proposal_digest', 'basis'}}
                unavailable = contracts.CollaborationProposal.create(**values,
                    basis=replace(proposal.basis, inputs=(missing,)))
                with self.assertRaisesRegex(AssertionError, 'unavailable'):
                    harness.assert_review_proposal(unavailable, agents)
                with self.assertRaisesRegex(AssertionError, 'REVIEW'):
                    harness.assert_review_proposal(scoped_proposal(agents, mode='draft'), agents)
        recovery = harness.selected_spec('F04-FAILURE-RECOVERY')
        request = harness.consultation_request(recovery, ('character_custodian',),
            text=recovery['recovery_instruction'], references=False)
        self.assertNotIn(recovery['candidate_text'], request)
        self.assertIn('finished character candidate', request)

    def test_drafted_inputs_are_not_unresolved_review_failures(self):
        from dataclasses import replace
        from story_writing_agents import conversations as contracts
        for mode in ('draft', 'review'):
            proposal = scoped_proposal(mode=mode)
            history, _, _ = scoped_executed_history(proposal)
            replies = [e for e in history if isinstance(e.payload, contracts.CollaborationSpecialistReplyPayload)]
            synthesis = next(e.payload for e in history if isinstance(e.payload, contracts.CollaborationLeadSynthesisPayload))
            harness.assert_scoped_synthesis(proposal, replies, synthesis)
            for assessment in ((contracts.CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE,)
                               if mode == 'draft' else
                               (contracts.CollaborationSpecialistAssessment.CONCERN,
                                contracts.CollaborationSpecialistAssessment.CONFLICT,
                                contracts.CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)):
                changed = [replace(replies[0], payload=replace(replies[0].payload, assessment=assessment))]
                with self.assertRaisesRegex(AssertionError, 'unresolved'):
                    harness.assert_scoped_synthesis(proposal, changed, synthesis)
                harness.assert_scoped_synthesis(proposal, changed,
                    replace(synthesis, unresolved_input_ids=proposal.assignments[0].input_ids))
            with self.assertRaisesRegex(AssertionError, 'task scope'):
                harness.assert_scoped_synthesis(proposal, replies,
                    replace(synthesis, authoring_scope=replace(proposal.authoring_scope, task_id='wrong_task')))
            wrong_mode = harness.SpecialistAssignmentMode.REVIEW if mode == 'draft' else harness.SpecialistAssignmentMode.DRAFT
            wrong = replace(replies[0].payload, mode=wrong_mode,
                assessment=contracts.CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
            with self.assertRaisesRegex(AssertionError, 'mode'):
                harness.assert_scoped_synthesis(proposal, [replace(replies[0], payload=wrong)], synthesis)
        # A DRAFTED payload cannot even be constructed in REVIEW mode.
        with self.assertRaises(ValueError):
            replace(replies[0].payload, mode=harness.SpecialistAssignmentMode.REVIEW,
                    assessment=contracts.CollaborationSpecialistAssessment.DRAFTED)

    def test_workflow_outcomes_keep_story_judgment_out_of_direct_cases(self):
        from types import SimpleNamespace
        from dataclasses import replace
        from story_writing_agents import conversations as contracts
        from story_writing_agents.specialist_routing import AuthoringScope, AuthoringActivity
        from tests import collaboration_contract_fixture as fixture
        scope = AuthoringScope(AuthoringActivity.WORKFLOW, (), (), 'Only workflow or literal lookup.', 'workflow_task', 1)
        for kind in (harness.LeadOutcomeKind.ANSWER, harness.LeadOutcomeKind.QUESTION,
                     harness.LeadOutcomeKind.CRITIQUE):
            # Verified contextual receipts remain compatible with a fresh workflow scope.
            judgment = contracts.CollaborationLeadJudgmentPayload(kind, 'Workflow result.',
                fixture.make_lead_evidence(), authoring_scope=scope)
            history = [SimpleNamespace(payload=judgment)]
            result = SimpleNamespace(kind=kind, proposal=None)
            harness.assert_workflow_judgment(history, result)
            story = scoped_proposal().authoring_scope
            with self.assertRaisesRegex(AssertionError, 'workflow-only'):
                harness.assert_workflow_judgment([SimpleNamespace(payload=replace(judgment, authoring_scope=story))], result)
            with self.assertRaisesRegex(AssertionError, 'reused'):
                harness.assert_workflow_judgment([SimpleNamespace(payload=SimpleNamespace(authoring_scope=replace(story, task_id=scope.task_id))), *history], result)
        for case_id, kind in (('L06A-QUESTION', harness.LeadOutcomeKind.QUESTION),):
            judgment = contracts.CollaborationLeadJudgmentPayload(kind, 'Workflow result.',
                fixture.make_lead_evidence(), authoring_scope=scope)
            case = SimpleNamespace(spec=harness.selected_spec(case_id),
                lead=mock.AsyncMock(return_value=SimpleNamespace(kind=kind, proposal=None)),
                history=lambda: [SimpleNamespace(payload=judgment)])
            asyncio.run(harness.ConversationCase.dispatch(case))
            case.lead.assert_awaited_once_with(case.spec['instruction'])

        recommendation = contracts.CollaborationLeadJudgmentPayload(
            harness.LeadOutcomeKind.RECOMMENDATION, 'Legacy workflow recommendation.',
            fixture.make_lead_evidence(), authoring_scope=scope)
        with self.assertRaisesRegex(AssertionError, 'recommendation authority'):
            harness.assert_workflow_judgment([SimpleNamespace(payload=recommendation)],
                SimpleNamespace(kind=harness.LeadOutcomeKind.RECOMMENDATION))

    def test_l06c_requires_exact_pending_plot_recommendation_without_execution(self):
        from dataclasses import fields, replace
        from types import SimpleNamespace
        from story_writing_agents import conversations as contracts
        from story_writing_agents.specialist_routing import ArtifactReviewDomain
        from tests.recommendation_fixture import RecommendationCase, action, recommendation

        runtime_case = RecommendationCase()
        self.addCleanup(runtime_case.doCleanups)
        runtime_case.setUp()
        conversation_id = runtime_case.conversation('L06C pending Plot recommendation')
        spec = harness.selected_spec('L06C-RECOMMENDATION')
        runtime_case.backend.enqueue(recommendation(message=spec['candidate_text'],
            recommended_action=action(kind='prepare_consultation',
                purpose='Prepare a separately confirmed chronology review.',
                instruction='Prepare a Plot REVIEW proposal for the supplied finished outline.')))
        observer = SimpleNamespace(calls=[])
        turns = []

        async def lead(text):
            turn = await runtime_case.runtime.lead_turn(runtime_case.session_id, conversation_id, text)
            observer.calls[:] = [{'agent': agent} for agent, _ in runtime_case.backend.call_records]
            turns.append(turn)
            return turn

        case = SimpleNamespace(spec=spec, lead=mock.AsyncMock(side_effect=lead), observer=observer,
            history=lambda: runtime_case.history(conversation_id))
        before = runtime_case.snapshots()
        asyncio.run(harness.ConversationCase.dispatch(case))
        case.lead.assert_awaited_once_with(spec['instruction'] + '\nFinished user-supplied outline:\n'
                                          + spec['candidate_text'])
        self.assertEqual(len(turns), 1)
        turn = turns[0]
        history = case.history()
        request = turn.recommendation_request
        self.assertIs(harness.assert_pending_plot_recommendation(history, turn, observer.calls), request)
        self.assertEqual(runtime_case.snapshots(), before)
        runtime_case.assert_no_downstream(conversation_id)
        self.assertEqual(sum(type(e.payload) is contracts.CollaborationMessagePayload
                             and e.direction is contracts.CollaborationDirection.USER_TO_LEAD for e in history), 1)
        self.assertIs(runtime_case.projection(conversation_id).current_recommendation_state,
                      contracts.LeadRecommendationState.PENDING)
        scope = request.authoring_scope

        def returned(**changes):
            values = {field.name: getattr(turn, field.name) for field in fields(turn)}
            values.update(changes)
            return SimpleNamespace(**values)

        profiles = tuple(replace(profile, agent_id='execution_specialist')
                         if profile.agent_id == 'plot_architect' else profile
                         for profile in request.basis.agent_profiles)
        nonplot = replace(request, specialist_ids=('execution_specialist',),
            basis=replace(request.basis, agent_profiles=profiles),
            authoring_scope=replace(scope, target_paths=(), domains=(ArtifactReviewDomain.TONE_EXECUTION,)))
        invalid = (
            ('absent request', returned(recommendation_request=None), 'typed recommendation'),
            ('absent scope', returned(recommendation_request=replace(request, authoring_scope=None)), 'REVIEW scope'),
            ('missing Plot', returned(recommendation_request=nonplot), 'required Plot'),
            ('foreign task', returned(recommendation_request=replace(request,
                authoring_scope=replace(scope, task_id='foreign_task'))), 'durable tail'),
            ('fabricated request', returned(recommendation_request=replace(request,
                request_id='fabricated_request')), 'durable tail'),
            ('foreign conversation', returned(conversation_id='foreign_conversation'), 'durable tail'),
            ('fabricated proposal', returned(proposal=object()), 'downstream proposal'),
            ('fabricated Apply', returned(artifact_application_request=object()), 'downstream proposal'),
            ('fabricated Commit', returned(story_commit_request=object()), 'downstream proposal'),
        )
        for name, corrupted, message in invalid:
            with self.subTest(case=name), self.assertRaisesRegex(AssertionError, message):
                harness.assert_pending_plot_recommendation(history, corrupted, observer.calls)
        with self.assertRaisesRegex(AssertionError, 'unconfirmed downstream'):
            harness.assert_pending_plot_recommendation(history, turn,
                [*observer.calls, {'agent': 'plot_architect'}])
        source = next(e for e in history if e.event_id == request.source_recommendation_event_id)
        for changes, message in (({'authoring_scope': replace(scope, task_id='foreign_task')}, 'task scope'),
                                  ({'message': source.payload.message + 'changed'}, 'digest differs')):
            changed_history = tuple(replace(e, payload=replace(source.payload, **changes))
                                    if e.event_id == source.event_id else e for e in history)
            with self.assertRaisesRegex(AssertionError, message):
                harness.assert_pending_plot_recommendation(changed_history, turn, observer.calls)
        self.assertEqual(case.history(), history)
        self.assertEqual(runtime_case.snapshots(), before)

    def test_missing_marker_rejects(self):
        directory = Path(self.calls[3]["directory"])
        value = json.loads((directory / "command.private.json").read_text())
        payload = json.loads(value["argv"][-1])
        del payload["i"]["response_correction"]
        value["argv"][-1] = json.dumps(payload)
        (directory / "command.private.json").write_text(json.dumps(value))
        self.calls[3]["payload_sha256"] = harness.digest(value["argv"][-1].encode())
        self.calls[3]["payload_semantic_sha256"] = harness.semantic_digest(payload)
        self.calls[3]["payload_bytes"] = len(value["argv"][-1].encode())
        request = json.loads(self.exchanges[9]["content"])
        request["payload"] = payload
        self.exchanges[9]["content"] = json.dumps(request)
        response = json.loads(self.exchanges[10]["content"])
        response["actual_transport_utf8_bytes"] = self.calls[3]["payload_bytes"]
        self.exchanges[10]["content"] = json.dumps(response)
        with self.assertRaisesRegex(AssertionError, "Missing or multiple"):
            self.audit()

    def test_wrong_native_thread_rejects(self):
        self.calls[3]["prior_thread_id"] = "wrong-thread"
        value = json.loads(self.exchanges[9]["content"])
        value["prior_thread_id"] = "wrong-thread"
        self.exchanges[9]["content"] = json.dumps(value)
        with self.assertRaisesRegex(AssertionError, "fresh prelude thread"):
            self.audit()

    def test_wrong_durable_invocation_rejects(self):
        self.history[7]["payload"]["invocation_id"] = "other-invocation"
        with self.assertRaisesRegex(AssertionError, "Wrong durable invocation"):
            self.audit()

    def test_extra_correction_rejects(self):
        self.history.append(copy.deepcopy(self.history[-1]))
        with self.assertRaisesRegex(AssertionError, "durable Lead judgment"):
            self.audit()
        self.history.pop()
        extra = copy.deepcopy(self.calls[-1])
        extra["number"] = 5
        # Direct proof input isolates duplicate-correction rejection from capture matching.
        entries = []
        for call in [*self.calls, extra]:
            payload, _ = harness.call_input(call)
            entries.append(dict(call=call, payload=payload,
                                outcome="rejected" if call["number"] == 2 else "accepted",
                                code="answer_validation_failed" if call["number"] == 2 else "accepted",
                                terminal=json.loads((Path(call["directory"]) / "stdout.raw").read_bytes())))
        with self.assertRaisesRegex(AssertionError, "Missing or multiple"):
            harness.prove_lead_corrections(entries, self.history)

    def test_mismatched_native_bytes_reject(self):
        (Path(self.calls[1]["directory"]) / "stdout.raw").write_bytes(b"changed")
        with self.assertRaisesRegex(AssertionError, "native stream bytes differ"):
            self.audit()

    def test_rejected_action_and_correlation_must_match(self):
        entries = []
        for call in self.calls:
            payload, _ = harness.call_input(call)
            entries.append(dict(call=call, payload=payload,
                                outcome="rejected" if call["number"] == 2 else "accepted",
                                code="answer_validation_failed" if call["number"] == 2 else "accepted",
                                terminal=json.loads((Path(call["directory"]) / "stdout.raw").read_bytes())))
        for field in ("correlation", "kind"):
            with self.subTest(field=field):
                changed = copy.deepcopy(entries)
                output = changed[1]["terminal"]["structured_output"]
                if field == "correlation":
                    output["correlation"] = "foreign"
                else:
                    output["action"]["kind"] = "search"
                with self.assertRaisesRegex(AssertionError, "correlation or action"):
                    harness.prove_lead_corrections(changed, self.history)

    def test_resume_accepts_only_proven_fresh_handoff(self):
        audit = self.audit()
        calls = copy.deepcopy(self.calls[1:])
        harness.assert_resumed_call_chain(calls, "old-thread", audit)
        with self.assertRaisesRegex(AssertionError, "Unexplained fresh"):
            harness.assert_resumed_call_chain(calls, "old-thread", [])
        calls[0]["prior_thread_id"] = None
        with self.assertRaisesRegex(AssertionError, "First resumed"):
            harness.assert_resumed_call_chain(calls, "old-thread", audit)

    def test_consultation_instructions_use_one_recall_marker(self):
        marked_cases = set()
        for spec in harness.load_inventory()["cases"]:
            with self.subTest(case=spec["id"]):
                markers = re.findall(r"FLOW623-[A-Z0-9-]+", spec["instruction"])
                self.assertTrue(all(marker == harness.MARKER for marker in markers))
                if markers:
                    marked_cases.add(spec["id"])
        self.assertEqual(
            marked_cases,
            {"L01-PRO-HIGH-PTY", "L02-PRO-LOW-ONE", "L04-SONNET-ONE"},
        )

    def test_fresh_cumulative_global_and_stage_bounds(self):
        inventory = harness.load_inventory()
        self.assertEqual(sum(s["max_calls"] for s in inventory["cases"]), 228)
        self.assertEqual(harness.selected_spec("F04-FAILURE-RECOVERY")["max_calls"], 24)
        self.assertEqual(inventory["budget"]["matrix_max_calls"], 312)
        self.assertEqual(harness.stage_seconds(7), 1440)
        self.assertEqual(harness.stage_seconds(12), 2340)
        with mock.patch.dict(os.environ, {"STORY_LIVE_BUDGET_ROOT": str(self.root),
                                          "STORY_LIVE_PRIOR_CASE_SUBMISSIONS": "8"}):
            observer = harness.CallEvidence(self.root, harness.selected_spec("L04-SONNET-ONE"), self.root)
            self.assertEqual(observer.effective_case_cap, 26)
            observer.calls = [{}] * 17
            observer.reserve()
            observer.calls.append({})
            with self.assertRaisesRegex(AssertionError, "Per-case"):
                observer.reserve()
        with mock.patch.dict(os.environ, {"STORY_LIVE_BUDGET_ROOT": str(self.root),
                                          "STORY_LIVE_PRIOR_CASE_SUBMISSIONS": "0"}):
            observer = harness.CallEvidence(self.root, harness.selected_spec("L06A-QUESTION"), self.root)
            ledger = self.root / "call-budget.json"
            ledger.write_text(json.dumps({"reserved": 311, "started_at": harness.time.time()}))
            observer.reserve()
            self.assertEqual(json.loads(ledger.read_text())["reserved"], 312)
            with self.assertRaisesRegex(AssertionError, "matrix physical-call cap"):
                observer.reserve()


class LiveRecoveryCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="live-recovery-coverage-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_lost_authority_restores_exact_nonempty_artifact_baseline(self):
        workspace = mock.Mock()
        workspace.path.side_effect = lambda name: self.root / name
        baseline = {}
        for name in harness.SEEDED_ARTIFACT_PATHS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Original fixture " + name)
            baseline[name] = harness.digest(path.read_bytes())
        manifest = self.root / "initial-artifacts.private.json"
        harness.save(manifest, baseline)
        original = manifest.read_bytes()
        checkpoint = {"artifact_baseline_sha256": harness.digest(original)}
        restored = harness.restore_artifact_baseline(self.root, workspace, checkpoint)
        self.assertEqual(restored, baseline)
        for invalid in ({}, dict(list(baseline.items())[1:])):
            with self.subTest(invalid_entries=len(invalid)):
                harness.save(manifest, invalid)
                matching = {"artifact_baseline_sha256": harness.digest(manifest.read_bytes())}
                with self.assertRaisesRegex(AssertionError, "incomplete"):
                    harness.restore_artifact_baseline(self.root, workspace, matching)
        manifest.write_bytes(original + b" ")
        with self.assertRaisesRegex(AssertionError, "checkpoint differs"):
            harness.restore_artifact_baseline(self.root, workspace, checkpoint)
        manifest.unlink()
        with self.assertRaises(FileNotFoundError):
            harness.restore_artifact_baseline(self.root, workspace, checkpoint)
        manifest.write_bytes(original)
        path = workspace.path(next(iter(baseline)))
        path.write_text("Changed after original checkpoint")
        with self.assertRaisesRegex(AssertionError, "artifact changed"):
            harness.restore_artifact_baseline(self.root, workspace, checkpoint)
        with self.assertRaisesRegex(AssertionError, "artifact changed"):
            harness.assert_seeded_artifacts(workspace, restored)

    def recovery_fixture(self):
        from types import SimpleNamespace
        from dataclasses import replace
        from story_writing_agents import conversations as contracts
        from tests import collaboration_contract_fixture as fixture

        old_proposal = scoped_proposal(('plot_architect', 'character_custodian'), proposal_id='failed_proposal')
        failed, _, _ = scoped_executed_history(
            old_proposal, failed_assignment_id='assignment_character_custodian')
        proposal = scoped_proposal(('character_custodian',), proposal_id='recovery_proposal')
        recovered, proposal, _ = scoped_executed_history(proposal)
        recovered = tuple(replace(e, payload=replace(
            e.payload, assessment=contracts.CollaborationSpecialistAssessment.CONCERN))
            if isinstance(e.payload, contracts.CollaborationSpecialistReplyPayload) else
            replace(e, payload=replace(e.payload, unresolved_input_ids=proposal.assignments[0].input_ids))
            if isinstance(e.payload, contracts.CollaborationLeadSynthesisPayload) else e
            for e in recovered)
        proposed = SimpleNamespace(step_id=recovered[0].step_id, proposal=proposal,
                                   confirmation_request_id="new-confirm")
        completed = SimpleNamespace(step_id=proposed.step_id,
                                    state=harness.CollaborationStepState.COMPLETED)
        for name in ("old-call", "new-call"):
            directory = self.root / name
            directory.mkdir(exist_ok=True)
            harness.save(directory / "staged.sanitized.json", {"sha256": "same-definition", "bytes": 10})
        faulted = dict(agent="character_custodian", vendor="antigravity", model="flash",
                       fault="nonzero", thread_id="failed-native", directory=str(self.root / "old-call"))
        fresh = dict(agent="character_custodian", vendor="antigravity", model="flash",
                     fault=None, thread_id="fresh-native", prior_thread_id=None, resumed=False,
                     native_status="SUCCESS", delivered_returncode=0,
                     directory=str(self.root / "new-call"))
        return failed, recovered, proposed, completed, faulted, fresh

    def test_failed_character_recovery_requires_fresh_same_agent_and_complete_history(self):
        from dataclasses import replace
        failed, recovered, proposed, completed, faulted, fresh = self.recovery_fixture()
        prefix = harness.canonical_history(failed)
        history = failed + recovered
        def audit(rows=None, events=None, saved_prefix=None):
            return harness.assert_character_recovery(
                history if events is None else events, prefix if saved_prefix is None else saved_prefix,
                faulted, [fresh] if rows is None else rows, proposed, completed)
        self.assertTrue(audit()["fresh_thread"])
        # Metadata compatibility only; native recovery acceptance belongs to the adapter audit.
        self.assertTrue(audit(rows=[dict(fresh, native_status="ERROR")])["fresh_thread"])
        for changed in ([], [dict(fresh, agent="plot_architect")],
                        [dict(fresh, prior_thread_id="failed-native", resumed=True)],
                        [dict(fresh, thread_id="failed-native")], [dict(fresh, model="different")],
                        [dict(fresh, delivered_returncode=17)]):
            with self.subTest(calls=changed), self.assertRaises(AssertionError):
                audit(rows=changed)
        for payload_type in (harness.CollaborationSpecialistReplyPayload,
                             harness.CollaborationLeadSynthesisPayload,
                             harness.CollaborationExecutionResult):
            with self.subTest(missing=payload_type.__name__), self.assertRaises(AssertionError):
                audit(events=failed + tuple(e for e in recovered if not isinstance(e.payload, payload_type)))
        no_confirm = failed + tuple(e for e in recovered
                                    if getattr(e.payload, "state", None) is not harness.CollaborationStepState.CONFIRMED)
        with self.assertRaisesRegex(AssertionError, "fresh Confirm"):
            audit(events=no_confirm)
        for field, value in (("unresolved_input_ids", ()), ("reply_event_ids", ("wrong-reply",)),
                             ("proposal_id", "different-proposal")):
            events = failed + tuple(replace(e, payload=replace(e.payload, **{field: value}))
                if isinstance(e.payload, harness.CollaborationLeadSynthesisPayload) else e for e in recovered)
            with self.subTest(field=field), self.assertRaises(AssertionError):
                audit(events=events)
        bad_prefix = copy.deepcopy(prefix)
        bad_prefix[-1]["event_id"] = "rewritten-failure"
        with self.assertRaisesRegex(AssertionError, "rewrote"):
            audit(saved_prefix=bad_prefix)

    async def test_failure_recovery_uses_new_lead_proposal_and_confirmation(self):
        from types import SimpleNamespace, MethodType
        failed, recovered, proposed, completed, faulted, fresh = self.recovery_fixture()
        failed_turn = SimpleNamespace(step_id="failed-step", confirmation_request_id="old-confirm",
            proposal=scoped_proposal(("plot_architect", "character_custodian"), proposal_id="failed_proposal"))
        for early_dispatch in (False, True):
            with self.subTest(early_dispatch=early_dispatch):
                order = []
                case = mock.Mock()
                case.directory = self.root
                case.spec = harness.selected_spec("F04-FAILURE-RECOVERY")
                case.observer.calls = []
                case.observer.fault_consumed = False
                case.runtime._run_specialist_assignment = mock.AsyncMock()
                events = []
                case.history.side_effect = lambda: tuple(events)
                async def propose(agents, **kwargs):
                    order.append(("propose", agents))
                    self.assertFalse(kwargs["references"])
                    if len(order) == 1:
                        return failed_turn
                    self.assertEqual(kwargs["text"], case.spec["recovery_instruction"])
                    case.observer.calls.append(dict(agent="character_custodian" if early_dispatch else "lead_orchestrator"))
                    return proposed
                async def confirm(turn):
                    order.append(("confirm", turn.step_id))
                    if turn is failed_turn:
                        case.observer.calls.append(faulted)
                        case.observer.fault_consumed = True
                        events.extend(failed)
                        return SimpleNamespace(state=harness.CollaborationStepState.FAILED)
                    case.observer.calls.append(fresh)
                    events.extend(recovered)
                    return completed
                case.propose = mock.AsyncMock(side_effect=propose)
                case.confirm = mock.AsyncMock(side_effect=confirm)
                case.recover_failed_character = MethodType(harness.ConversationCase.recover_failed_character, case)
                with mock.patch.object(harness, "project_collaboration_history", return_value=SimpleNamespace(
                        current_state=harness.CollaborationStepState.COMPLETED)):
                    if early_dispatch:
                        with self.assertRaisesRegex(AssertionError, "before Confirm"):
                            await harness.ConversationCase.interrupted_or_failed(case, cancel=False)
                        self.assertEqual(case.confirm.await_count, 1)
                    else:
                        await harness.ConversationCase.interrupted_or_failed(case, cancel=False)
                        self.assertEqual([item[0] for item in order], ["propose", "confirm", "propose", "confirm"])
                        self.assertEqual(order[2][1], ("character_custodian",))
                        self.assertTrue((self.root / "character-recovery.sanitized.json").exists())
                    self.assertEqual(case.observer.fault, None)


class LiveReferenceDriftTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_drift_accepts_only_exact_authority_rejection(self):
        message = "lead proposal authority drift requires regeneration and confirmation"
        errors = (
            harness.BackendProtocolError(message),
            harness.BackendProtocolError("unrelated backend failure"),
            ValueError(message),
        )
        with tempfile.TemporaryDirectory(prefix="reference-drift-handler-") as directory:
            outline = Path(directory) / "outline.md"
            for index, error in enumerate(errors):
                with self.subTest(error=type(error).__name__, exact=index == 0):
                    case = mock.Mock()
                    case.workspace.path.return_value = outline
                    case.propose = mock.AsyncMock(return_value=mock.sentinel.proposal)
                    case.observer.calls = []
                    case.runtime.decide_lead_step = mock.AsyncMock(side_effect=error)
                    with mock.patch.object(harness, "decision", return_value=mock.sentinel.decision):
                        if index == 0:
                            await harness.ConversationCase.reference_drift(case)
                        else:
                            with self.assertRaises(type(error)) as raised:
                                await harness.ConversationCase.reference_drift(case)
                            self.assertIs(raised.exception, error)
                    case.propose.assert_awaited_once_with()
                    case.runtime.decide_lead_step.assert_awaited_once_with(mock.sentinel.decision)
                    self.assertEqual(case.observer.calls, [])
                    self.assertEqual(outline.read_text(), harness.OUTLINE + "External fixture drift.\n")


class LiveHarnessChildCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_fault_child_timeout_cancellation_and_success_are_joined(self):
        for failure in (asyncio.TimeoutError(), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                child = mock.Mock(pid=123456, returncode=None)
                child.wait = mock.AsyncMock(return_value=-9)
                async def waits(awaitable, timeout):
                    await awaitable
                    if timeout == 1:
                        raise failure
                    raise asyncio.TimeoutError()
                with mock.patch.object(harness.asyncio, "wait_for", side_effect=waits), mock.patch.object(
                    harness.os, "killpg", create=True
                ) as killpg:
                    with self.assertRaises(type(failure)):
                        await harness.wait_owned_fault_child(child, 1)
                self.assertEqual(child.wait.await_count, 3)
                if os.name == "posix":
                    self.assertEqual(killpg.call_count, 2)
        child = mock.Mock(returncode=86)
        child.wait = mock.AsyncMock(return_value=86)
        self.assertEqual(await harness.wait_owned_fault_child(child, 1), 86)
        child.wait.assert_awaited_once()


class LiveTerminalCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_failure_survives_cleanup_and_standalone_cleanup_fails(self):
        import sys

        with tempfile.TemporaryDirectory() as directory:
            terminal = mock.Mock(label="owned", case=mock.Mock(directory=Path(directory)))
            terminal.close = mock.AsyncMock(side_effect=RuntimeError("cleanup"))
            original = ValueError("primary")
            with self.assertRaises(ValueError) as caught:
                try:
                    raise original
                finally:
                    await harness.close_terminal(terminal, sys.exc_info()[1])
            self.assertIs(caught.exception, original)
            terminal.close.assert_awaited_once()
            record = json.loads((Path(directory) / "owned.cleanup-error.sanitized.json").read_text())
            self.assertEqual(record, {"primary_error_type": "ValueError", "cleanup_error_type": "RuntimeError"})
            with self.assertRaises(RuntimeError):
                await harness.close_terminal(terminal)
            terminal.close = mock.AsyncMock()
            await harness.close_terminal(terminal)
            terminal.close.assert_awaited_once()


class LiveBudgetContinuationTests(unittest.TestCase):
    def test_budget_next_intent_injection_preserves_exact_completed_native_fields(self):
        contract = {
            "evidence_ids": ["evidence-reference"], "evidence_status": "verified",
            "scope": "story_specific", "reply_event_ids": ["reply-plot", "reply-character", "reply-execution"],
            "unresolved_input_ids": ["user_input", "reference_1"],
        }
        from story_writing_agents.conversations.events import authoring_scope_to_dict
        contract["authoring_scope"] = authoring_scope_to_dict(scoped_proposal(harness.BUDGET_REVIEWERS).authoring_scope)
        payload = {"c": "synthetic-synthesis-correlation", "i": {
            "workflow": "lead_synthesis", "answer_contract": contract,
            "inputs": [{"input_id": "user_input", "kind": "user_input"},
                       {"input_id": "reference_1", "kind": "core_document"}],
        }}
        native = {
            "status": "SUCCESS", "conversation_id": "synthetic-thread", "json_schema": {"type": "object"},
            "structured_output": {"correlation": payload["c"], "action": {
                "kind": "answer", "used_handles": [], "result": {
                    **copy.deepcopy(contract), "message": "A complete visible synthesis.", "next_intent": None, "recommended_action": None,
                },
            }}, "usage": {"input_tokens": 13, "output_tokens": 7},
        }
        original = copy.deepcopy(native)
        original_payload = copy.deepcopy(payload)
        stdout = json.dumps(native, indent=2).encode()
        def mutate(value=stdout, request=payload, **changes):
            kwargs = {"agent": "lead_orchestrator", "returncode": 0, "physical_calls_spent": 10}
            kwargs.update(changes)
            return harness.inject_budget_next_intent(value, request, **kwargs)

        delivered, proof = mutate()
        actual = json.loads(delivered)
        intent = actual["structured_output"]["action"]["result"]["next_intent"]
        self.assertEqual([a["agent_id"] for a in intent["assignments"]], list(harness.BUDGET_REVIEWERS))
        for assignment in intent["assignments"]:
            self.assertEqual(assignment["input_ids"], ["user_input", "reference_1", *contract["reply_event_ids"]])
            self.assertEqual(assignment["mode"], "review")
            self.assertEqual(assignment["review_basis"]["candidate_input_ids"], ["reference_1"])
        self.assertEqual(intent["authoring_scope"], contract["authoring_scope"])
        actual["structured_output"]["action"]["result"]["next_intent"] = None
        self.assertEqual(actual, original)
        self.assertEqual(native, original)
        self.assertEqual(payload, original_payload)
        self.assertEqual(proof["minimum_required_calls"], 8)
        self.assertEqual(proof["remaining_calls"], 2)
        self.assertEqual(proof["path"], "structured_output.action.result.next_intent")
        self.assertTrue(proof["changed"])
        self.assertEqual(proof["original_stdout_sha256"], harness.digest(stdout))
        self.assertEqual(proof["delivered_stdout_sha256"], harness.digest(delivered))
        unchanged, observed = mutate(delivered)
        self.assertEqual(unchanged, delivered)
        self.assertFalse(observed["changed"])

        for changes in ({"agent": "plot_architect"}, {"returncode": 1},
                        {"physical_calls_spent": 4}, {"physical_calls_spent": 13},
                        {"physical_calls_spent": True}):
            with self.subTest(transport=changes), self.assertRaises(AssertionError):
                mutate(**changes)
        for workflow in ("lead_front_channel", "writing_artifact_reference_read"):
            wrong = copy.deepcopy(payload)
            wrong["i"]["workflow"] = workflow
            with self.subTest(workflow=workflow), self.assertRaises(AssertionError):
                mutate(request=wrong)
        for mutation in ("status", "correlation", "kind", "missing", "evidence", "unresolved", "recommended_action"):
            wrong = copy.deepcopy(native)
            action = wrong["structured_output"]["action"]
            if mutation == "status":
                wrong["status"] = "ERROR"
            elif mutation == "correlation":
                wrong["structured_output"]["correlation"] = "another-correlation"
            elif mutation == "kind":
                action["kind"] = "search"
            elif mutation == "missing":
                del action["result"]["message"]
            elif mutation == "evidence":
                action["result"]["evidence_ids"] = []
            elif mutation == "recommended_action":
                action["result"]["recommended_action"] = {"kind": "prepare_artifact"}
            else:
                action["result"]["unresolved_input_ids"] = []
            with self.subTest(native=mutation), self.assertRaises(AssertionError):
                mutate(json.dumps(wrong).encode())
