"""Existing Lead runtime setup fixtures with explicit ownership."""
from __future__ import annotations
import copy
import json
import subprocess
import tempfile
import unittest
from story_writing_agents.lead_execution import (
    confirmed_step_execution as confirmed_execution,
)
from unittest import mock
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
    LEAD_ORCHESTRATOR,
)
from story_writing_agents.conversations import (
    CollaborationLeadJudgmentPayload,
    LeadOutcomeKind,
    CollaborationAssignmentDispatchPayload,
    CollaborationConfirmationDecision,
    CollaborationExecutionResult,
    CollaborationLeadSynthesisPayload,
    CollaborationNextAction,
    project_collaboration_history,
)
from story_writing_agents import ReplyTraceOrigin, StoryRuntime, Workspace
from contextlib import contextmanager
from story_writing_agents.structured_output_validation import (
    diagnose_json_text,
    failure_issues,
)
from tests.provider_wire_fixture import (
    fixture_schema_node,
    selection_plan,
    lead_fixture_response,
    lead_fixture_result,
    result as wire_result,
)
from tests.helpers import materialize_pristine_initialized_workspace
from story_writing_agents.workspace.session_resources import provision_session_resources
from dataclasses import asdict
from tests.lead_fixtures.preparation_data import (
    LEAD_FIXTURE_READINESS_DOCUMENTS,
    READINESS_USER,
    STORY_USER_INPUT,
    current_proposal_outcome,
    event_payloads,
    fixture_assessment,
    search_history,
    submitted_calls,
    whole_book_assessment,
    lead_confirmation_decision,
    lead_specialist_outcome,
)
from tests.lead_fixtures.provider_responses import LeadBackend, workflow_material


def capture_execution_inputs(initialize, *args, **kwargs):
    with mock.patch.object(
        confirmed_execution.ConfirmedStepExecution, "__init__", autospec=True,
        side_effect=confirmed_execution.ConfirmedStepExecution.__init__,
    ) as constructor:
        try:
            result = initialize(*args, **kwargs)
            arguments = constructor.call_args.kwargs
            return result, arguments["issued_capabilities"], arguments["live_executions"]
        finally:
            constructor.reset_mock()


class LeadOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def _decide(self, turn, kind): return self.runtime.decide_lead_step(lead_confirmation_decision(turn, kind))

    def _set_up_workspace(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="lead-orchestration-")
        self.addCleanup(temporary.cleanup)
        self.control_workspace = materialize_pristine_initialized_workspace(f"{temporary.name}/story")
        self.session_id = "story_session"
        ready = provision_session_resources(self.control_workspace.root, self.session_id,)
        self.session_resources = ready
        self.workspace = Workspace(ready.identity.worktree_root).require_ready()
        self.workspace.path('core_documents/global_outline.md').write_text(
            'Outline first line.\nOutline second line.\n', encoding='utf-8')
        self.workspace.path('core_documents/character_sheets.md').write_text(
            'Mara wants safety.\nMara fears exposure.\n', encoding='utf-8')
        part = self.workspace.path("manuscript/part-01")
        part.mkdir(parents=True, exist_ok=True)
        (part / "opening.md").write_text("Opening one.\nOpening two.\nOpening three.\n", encoding="utf-8",)

    def _set_up_fake_provider_boundary(self) -> None:
        # The orchestration fake has no native provider process or role file.
        boundary_patch = mock.patch('story_writing_agents.runtime_resources.session_worktree_access.session_provider_execution_workspace_manager',
            return_value=None)
        boundary_patch.start()
        self.addCleanup(boundary_patch.stop)

    def setUp(self) -> None:
        self._set_up_workspace()
        self.backend = LeadBackend(test_case=self)
        self._set_up_fake_provider_boundary()
        self.runtime = StoryRuntime(  # type: ignore[arg-type]
            self.control_workspace, self.backend,)


    async def _lead_turn(self, conversation_id, message, **kwargs):
        return await self.runtime.lead_turn(self.session_id, conversation_id, message, **kwargs)

    async def _rejected_pair(self, conversation_id, message, response, *, selection_group=None):
        if selection_group is None:
            self.backend.enqueue(response)
            self.backend.enqueue(response)
        else:
            self.backend.enqueue(response, control_plan=selection_plan(selection_group))
            self.backend.enqueue(response, control_plan=())
        before = len(self.backend.calls)
        result = await self._lead_turn(conversation_id, message)
        self.assertEqual(len(self.backend.calls) - before, 2 + int(selection_group is not None), result)
        self.assertIs(result.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        return result

    def conversation(self, subject: str = "Lead collaboration") -> str:
        return self.runtime.conversation_storage_services.catalog.create(self.session_id, subject,).conversation_id

    def history(self, conversation_id: str):
        return self.runtime.conversation_storage_services.event_store.read(self.session_id, conversation_id,)

    async def record_book_readiness(self, *, seed_before_capture=False, preserve_documents=False):
        documents = tuple((path, self.workspace.path(path).read_text(encoding="utf-8"))
                          for path, _ in LEAD_FIXTURE_READINESS_DOCUMENTS) if preserve_documents else LEAD_FIXTURE_READINESS_DOCUMENTS
        for path, content in documents:
            self.workspace.path(path).write_text(content, encoding="utf-8")
        if seed_before_capture:
            subprocess.run(["git", "add", "--", *(path for path, _ in LEAD_FIXTURE_READINESS_DOCUMENTS)], cwd=self.workspace.root, check=True)
            subprocess.run(["git", "commit", "-m", "Seed whole-book readiness inputs"], cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL)
        conversation_id = self.conversation("Explicit whole-book readiness")
        call_count, read_count = len(self.backend.calls), len(self.backend.reference_calls)

        def assess(call, provider):
            answer = "Visible lead response."
            return whole_book_assessment(call, answer,
                                          offered_inputs=provider.callback_payload(call)["task_input"]["available_input_sources"])

        self.backend.enqueue(assess, control_plan=selection_plan("readiness.core_documents"))
        user = READINESS_USER + " " + " ".join("${" + path + "}" for path, _ in LEAD_FIXTURE_READINESS_DOCUMENTS)
        result = await self.runtime.lead_turn(self.session_id, conversation_id, user)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.proposal)
        self.assertEqual((len(self.backend.calls) - call_count, len(self.backend.reference_calls) - read_count), (2, 1))
        self.assertEqual(tuple(item["content"] for item in self.backend.reference_contexts[-1]),
                         tuple(content for _, content in documents))
        readiness = self.runtime.inspect_core_document_readiness(self.session_id)
        self.assertEqual(readiness.complete_documents, tuple(path for path, _ in LEAD_FIXTURE_READINESS_DOCUMENTS),
            (result.kind, result.next_action, result.state, result.validation_details,
             any(item.core_document_readiness is not None for item in event_payloads(
                 self.history(conversation_id), CollaborationLeadJudgmentPayload)), readiness.remaining))
        return readiness


    async def proposal(self, conversation_id: str, *,
        text: str = "Please check the story direction.",
        assignments: list[dict[str, object]] | None = None,
        relation: str = "new_step",
        domains=("plot_structure",),
        authoring_assessment: dict[str, object] | None = None,
    ):
        (self.backend.enqueue(current_proposal_outcome(assignments or [{'agent_id':
            PLOT_ARCHITECT, 'instruction': 'Check the story direction against the request.',
            'input_ids': ['user_input']}], relation=relation, domains=domains,
            author_review_basis=True,
            authoring_assessment=authoring_assessment),
            control_plan=selection_plan("consultation.authoring.review")))
        return await self.runtime.lead_turn(self.session_id, conversation_id, text,)


    def _require_fixture_census(self, bound, expected_families):
        from collections import Counter
        from story_writing_agents.agent_context.manifest import ContextSourceKind
        self.assertEqual({item.owner.variant.branch_key.split(':partition:', 1)[0]
                          for item in bound.semantic_index.contracts}, expected_families)
        pairs = dict(bound.outer_inner_schema_ids)
        self.assertEqual(set(pairs), set(bound.action_index.expected_schema_ids))
        actions = Counter((item.owner.rules.literal_at(('action_type',)), item.owner.rules.property_names())
                          for item in bound.action_index.contracts)
        signatures = {'search': ('search_context', ('action_type', 'queries')),
                      'read': ('read_context', ('action_type', 'read_handles')),
                      'answer': ('submit_result', ('action_type', 'task_result'))}
        offers = bound.eligibility_offers
        physical_results = len(bound.semantic_index.expected_schema_ids)
        expected = Counter({signatures[name]: (physical_results if name == 'answer' else 1)
                            for name in offers.allowed_actions})
        if ('search' in offers.allowed_actions
                and ContextSourceKind.WRITING_ARTIFACT in offers.search_source_kinds):
            expected['search_context', ('action_type', 'artifact_directory_prefix', 'queries')] = 1
        self.assertEqual(actions, expected)
        self.assertEqual(Counter(value for value in pairs.values() if value is not None),
                         Counter(bound.semantic_index.expected_schema_ids))
        self.assertEqual(sum(value is None for value in pairs.values()),
                         sum(total for (action, _), total in expected.items()
                             if action != 'submit_result'))

    def _require_fixture_text_boundaries(
        self, bound, packet, *, authorization=None, synthesis=False, message_utf8_budget=32768):
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        from story_writing_agents.structured_output_validation import diagnose_json_text
        declared = bound.schema_value()
        native_projections = (('codex', declared), ('antigravity', AntigravityAdapter.native_output_schema(declared)))
        for vendor, schema in native_projections:
            bound.require_projection(schema, declared_lowering='integer_singleton_bounds' if vendor == 'antigravity' else 'identity')

        def require_text_graph(neighbor, valid, path, *, native_valid=None):
            native_valid = valid if native_valid is None else native_valid
            self.assertEqual(not bool(bound.rule_graph.findings(neighbor)), valid, path)
            # Invalid scalar strings use JSON escapes; large valid scalars use actual UTF-8.
            frame = json.dumps(neighbor, ensure_ascii=not valid)
            for vendor, schema in native_projections:
                self.assertEqual(not bool(diagnose_json_text(frame, schema)), native_valid, (vendor, path))

        # These governed Lead fields retain scalar/codepoint rules and genuine byte checks.
        root = ('agent_action', 'task_result')
        paths = [root + ('response_text',)]
        details = packet['agent_action']['task_result']['response_details']
        def governed(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (key == 'task_instructions' or key == 'consultation_purpose'
                            and 'follow_up_consultation' in path):
                        paths.append(path + (key,))
                    else:
                        governed(item, path + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    governed(item, path + (index,))
        for family in ('specialist_consultation_proposal', 'follow_up_consultation', 'proposal_drafting_request'):
            if family in details:
                governed(details[family], root + ('response_details', family))
        def text_neighbor(path, text):
            neighbor = copy.deepcopy(packet)
            if path != root + ('response_text',):
                neighbor['agent_action']['task_result']['response_text'] = 'a'
            node = neighbor
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = text
            return neighbor
        for path in paths:
            for text in ('a' * 32768, '\u4e00', '\U0001f642', '\U0001f642' * 8192):
                neighbor = text_neighbor(path, text)
                require_text_graph(neighbor, True, path)
                escaped = json.dumps(neighbor, ensure_ascii=False)
                if path == root + ('response_text',) and len(text.encode('utf-8')) > message_utf8_budget:
                    with self.assertRaises(ValueError):
                        bound.decode_response(escaped)
                else:
                    decoded = bound.decode_response(escaped)
                    self.assertTrue(decoded.belongs_to(bound))
                    if path == root + ('response_text',):
                        self.assertEqual(json.loads(decoded.answer_json)['message'], text)
            if path == root + ('response_text',) and message_utf8_budget < 32768:
                self.assertGreater(message_utf8_budget, 0)
                for text in ('a' * message_utf8_budget,
                             '\U0001f642' * (message_utf8_budget // 4) + 'a' * (message_utf8_budget % 4)):
                    at_budget = text_neighbor(path, text)
                    require_text_graph(at_budget, True, path)
                    decoded = bound.decode_response(json.dumps(at_budget, ensure_ascii=False))
                    self.assertTrue(decoded.belongs_to(bound))
                    self.assertEqual(json.loads(decoded.answer_json)['message'], text)
                    above_budget = text_neighbor(path, text + 'a')
                    require_text_graph(above_budget, True, path)
                    with self.assertRaises(ValueError):
                        bound.decode_response(json.dumps(above_budget, ensure_ascii=False))
            for text in ('a' * 32769, '\ud800', '\udfff', '\ud83d\ude42'):
                malformed = text_neighbor(path, text)
                require_text_graph(malformed, False, path, native_valid=text == '\ud83d\ude42')
                with self.assertRaises(ValueError):
                    bound.decode_response(json.dumps(malformed, ensure_ascii=False))
            # Escaped JSON pairs decode to a scalar; an explicit Python surrogate pair does not.
            escaped_pair = text_neighbor(path, '\U0001f642')
            self.assertIn('\\ud83d\\ude42', json.dumps(escaped_pair, ensure_ascii=True))
            self.assertTrue(bound.decode_response(json.dumps(escaped_pair, ensure_ascii=True)).belongs_to(bound))
            byte_overage = text_neighbor(path, '\U0001f642' * 8193)
            self.assertLessEqual(8193, 32768)
            self.assertGreater(len(('\U0001f642' * 8193).encode('utf-8')), 32768)
            require_text_graph(byte_overage, True, path)
            literal = json.dumps(byte_overage, ensure_ascii=False)
            self.assertLessEqual(len(literal.encode('utf-8')), 65536)
            if path == root + ('response_text',):
                with self.assertRaises(ValueError):
                    bound.decode_response(literal)
            else:
                decoded_overage = bound.decode_response(literal)
                self.assertTrue(decoded_overage.belongs_to(bound))
                if authorization is not None:
                    valid = bound.decode_response(json.dumps(packet, ensure_ascii=False))
                    bound.require_authorized_answer(valid, authorization)
                    if synthesis:
                        from story_writing_agents.lead_provider_contracts import LeadContractRejection
                        self.assertIs(bound.factory.constraints.continuation, True)
                        self.assertEqual(
                            path[:4], root + ('response_details', 'follow_up_consultation'))
                        assignment_issue = ('$.next_intent.assignments: each instruction must be '
                            'nonempty bounded UTF-8 text and satisfy ' 'review-criteria requirements')
                        expected = {('consultation_purpose',): (
                                '$.next_intent.purpose: expected nonempty bounded UTF-8 text'),
                            ('assignments', PLOT_ARCHITECT, 'task_instructions'): assignment_issue,
                            ('assignments', CHARACTER_CUSTODIAN, 'task_instructions'): assignment_issue,
                            ('assignments', EXECUTION_SPECIALIST, 'task_instructions'): assignment_issue, }[path[4:]]
                        with self.assertRaises(LeadContractRejection) as rejected:
                            bound.require_authorized_answer(decoded_overage, authorization)
                        self.assertIs(type(rejected.exception), LeadContractRejection)
                        self.assertEqual(rejected.exception.code, 'LEAD_OWNER_RULE_REJECTED')
                        findings = failure_issues(rejected.exception)
                        self.assertTrue(findings, path)
                        self.assertEqual(findings, (expected,), path)
                        continue
                    with self.assertRaises(BackendProtocolError):
                        bound.require_authorized_answer(decoded_overage, authorization)
        ordinary = details.get('specialist_consultation_proposal')
        if type(ordinary) is dict and 'consultation_purpose' in ordinary:
            ordinary_path = root + ('response_details', 'specialist_consultation_proposal', 'consultation_purpose')
            uncapped_purpose = text_neighbor(ordinary_path, 'a' * 32769)
            require_text_graph(uncapped_purpose, True, ordinary_path)
            decoded_purpose = bound.decode_response(json.dumps(uncapped_purpose, ensure_ascii=False))
            self.assertEqual(json.loads(decoded_purpose.answer_json)['proposal']['purpose'], 'a' * 32769)
            if authorization is not None:
                bound.require_authorized_answer(decoded_purpose, authorization)


    def _require_fixture_projections(self, bound, native_projections=None):
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        if native_projections is None:
            declared = bound.schema_value()
            native_projections = (('codex', declared),
                ('antigravity', AntigravityAdapter.native_output_schema(declared)))
        native_projections = tuple(row if len(row) == 3 else (*row, bound.rule_graph.provider_projection(
            row[1], declared_lowering='integer_singleton_bounds' if row[0] == 'antigravity' else 'identity'))
            for row in native_projections)
        def property_orders(value, path=()):
            if type(value) is dict:
                if type(value.get('properties')) is dict: yield path, tuple(value['properties'])
                for key, child in value.items(): yield from property_orders(child, path + (key,))
            elif type(value) is list:
                for index, child in enumerate(value): yield from property_orders(child, path + (index,))
        for provider, schema, owner in native_projections:
            self.assertIs(owner.owner, bound.rule_graph)
            bound.require_projection(schema, declared_lowering='integer_singleton_bounds'
                                     if provider == 'antigravity' else 'identity')
            self.assertEqual(owner.canonical_bytes, json.dumps(schema, ensure_ascii=False,
                separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8", "strict"))
            self.assertEqual(dict(property_orders(schema)), dict(property_orders(owner.schema_value())))
        return bound, native_projections

    def _require_fixture_field_neighbors(self, bound, packet, *, native_projections=None, prepared_projections=None):
        from contextlib import contextmanager
        import time
        from tests.provider_wire_fixture import field_neighbor_cases, field_neighbor_value, fixture_property_policies
        from story_writing_agents.structured_output_validation import diagnose_value
        action = packet['agent_action']; result = action.get('task_result', {})
        scope = result.get('response_details', {}).get('task_scope', {})
        identity = {'outer': action.get('schema_id'), 'inner': result.get('schema_id'),
            'semantic_schema_bytes': len(bound.semantic_index.rule_graph.canonical_bytes),
            'call_schema_bytes': len(bound.rule_graph.canonical_bytes),
                    'family': result.get('response_type'),
                    'profile': {key: scope.get(key) for key in ('intent', 'activity', 'domains', 'roles')}}
        totals, case_count, finished = {}, 0, False
        invocation = getattr(self, '_neighbor_invocation', 0) + 1; self._neighbor_invocation = invocation
        def checkpoint(name, status):
            print('WHOLE_FRONT_NEIGHBORS ' + json.dumps({'test_id': self.id(), 'invocation': invocation, 'packet': identity,
                'phase': name, 'status': status,
                'totals_kind': 'cumulative', 'final': status in ('complete', 'partial'),
                'case_count': case_count, 'counts_wall_cpu': totals[name]}, separators=(',', ':')), flush=True)
        @contextmanager
        def phase(name):
            started, cpu, complete = time.perf_counter(), time.process_time(), False
            try:
                yield
                complete = True
            finally:
                row = totals.setdefault(name, [0, 0, 0.0, 0.0]); row[0] += 1; row[1] += int(complete)
                row[2] += time.perf_counter() - started; row[3] += time.process_time() - cpu
                if not complete or row[0] % 64 == 0: checkpoint(name, 'checkpoint' if complete else 'interrupted')
        try:
            with phase('preparation'):
                prepared = (self._require_fixture_projections(bound, native_projections)
                            if prepared_projections is None else prepared_projections)
                self.assertIs(prepared[0], bound)
                native_projections = prepared[1]
                identity['native_schema_bytes'] = {provider: len(owner.canonical_bytes)
                    for provider, _schema, owner in native_projections}
                self.assertFalse(bound.rule_graph.findings(packet))
                baseline = bound.decode_response(json.dumps(packet))
                self.assertEqual(bound.decode_value(packet).domain_value(), baseline.domain_value())
                policies, required_fields = fixture_property_policies(packet, bound.rule_graph.schema_value())
                for provider, schema, owner in native_projections:
                    self.assertEqual(diagnose_value(packet, schema), owner.findings(packet), provider)
                    self.assertFalse(owner.findings(packet), provider)
                cases = field_neighbor_cases(packet, policies, required_fields); case_count = len(cases)
            for case in cases:
                coordinate, _path, _operation, _replacement, native_valid, foreign_path = case
                changed = field_neighbor_value(packet, case)
                if _path[:4] == ('agent_action', 'task_result', 'response_details', 'authoring_assessment') and _operation == 'set' and _replacement is None:
                    assessment = packet['agent_action']['task_result']['response_details']['authoring_assessment']
                    preparation_admission = (result.get('response_type') == 'proposal_drafting_recommendation'
                        and scope.get('intent') == 'authoring' and scope.get('activity') != 'workflow_guidance')
                    native_valid = not preparation_admission and (_path[4:] == ('direction',) and assessment['status'] != 'sufficient'
                        or _path[4:] == ('creative_scope',) and (assessment['status'] != 'sufficient' or bool(assessment['allowed_creative_actions'])))
                with phase('production-value-and-strict-findings'):
                    self.assertEqual(not bool(bound.rule_graph.findings(changed)), native_valid, coordinate)
                    production_valid = native_valid or foreign_path is not None and not policies[foreign_path]
                    if production_valid:
                        decoded = bound.decode_value(changed)
                        self.assertTrue(decoded.belongs_to(bound), coordinate)
                        if foreign_path is not None:
                            self.assertEqual(decoded.domain_value(), baseline.domain_value(), coordinate)
                    else:
                        with self.assertRaisesRegex(ValueError,
                                r'^LEAD_(ACTION_ENVELOPE|SCHEMA_ID|PAIRED_SCHEMA_ID|SCHEMA_RULE)_REJECTED$'):
                            bound.decode_value(changed)
                for provider, _schema, owner in native_projections:
                    with phase(provider + '-strict-projection'):
                        self.assertEqual(not bool(owner.findings(changed)), native_valid, (provider, coordinate))
            finished = True
        finally:
            for name in totals: checkpoint(name, 'complete' if finished else 'partial')



    def _require_slot_repair(self, guidance):
        residual = next(item for item in guidance['paired_semantic_requirements']['owner_requirements']
                        if item['binding_id'] == 'owner.dependency_slot_ids_unique')
        self.assertEqual((residual['revision'], residual['phase']), ('848-v1:dependency-slot-identity', 'selected'))
        self.assertEqual(residual['facts'], [{'path': '$.agent_action.task_result.response_details.task_scope.slots[*].id',
            'rule': 'DEPENDENCY_SLOT_IDS_UNIQUE', 'expected': {
                'requirement': 'dependency slot IDs must be unique within one authoring scope',
                'comparison': 'SLOT_ID_WITHIN_ONE_SCOPE'}}])

    def _require_revision_fixture(self, call, encoded, prior_scope, *, artifact=False):
        try:
            from story_writing_agents.antigravity_adapter import AntigravityAdapter
            bound, packet = call.contract, json.loads(encoded)
            self._require_fixture_census(bound, {f'lead-response-v9:family:{i}' for i in range(3 if artifact else 4)})
            self.assertEqual(bound.factory.constraints.task_revisions, ((prior_scope.task_id, prior_scope.task_revision + 1),))
            self.assertTrue(bound.decode_response(encoded).belongs_to(bound))
            schemas = (call.schema, AntigravityAdapter.native_output_schema(call.schema))
            self._require_fixture_field_neighbors(bound, packet, native_projections=tuple(zip(('codex', 'antigravity'), schemas)))
            values = {name: set() for name in ('response_type', 'action_type', 'activity', 'intent')}
            for member in bound.semantic_index.contracts:
                graph = member.rule_graph.schema_value()
                pending = [graph]
                while pending:
                    node = pending.pop()
                    if type(node) is list: pending.extend(node)
                    if type(node) is not dict: continue
                    pending.extend(node.values())
                    if 'task_scope' in node.get('properties', {}):
                        self.assertIn('authoring_assessment', node['required'])
                    for name in values.keys() & node.get('properties', {}).keys():
                        fields = [node['properties'][name]]
                        while fields:
                            field = fixture_schema_node(graph, fields.pop())
                            if 'anyOf' in field:
                                self.assertTrue(field['anyOf'], name)
                                fields.extend(field['anyOf'])
                            else:
                                self.assertEqual(field.get('type'), 'string', name)
                                self.assertTrue(field.get('enum'), name)
                                self.assertTrue(all(type(value) is str for value in field['enum']), name)
                                values[name].update(field['enum'])
            self.assertEqual(values['response_type'], {'answer', 'clarification_question', 'proposal_drafting_recommendation',
                'artifact_change_proposal' if artifact else 'specialist_consultation_proposal'})
            self.assertEqual(values['action_type'], {'draft_document_change_proposal' if artifact else 'draft_specialist_consultation_proposal'})
            self.assertEqual(values['intent'], {'authoring'})
            self.assertNotIn('workflow_guidance', values['activity'])
            if artifact: self.assertEqual(values['activity'], {'review_writing'})
            details = packet['agent_action']['task_result']['response_details']
            for field, key, value in (('task_scope', 'activity', 'draft_writing' if artifact else 'workflow_guidance'),
                    ('task_scope', 'intent', 'review_only'),
                    ('proposal_drafting_request', 'action_type', 'draft_specialist_consultation_proposal' if artifact else 'draft_document_change_proposal')):
                if field not in details: continue
                changed = copy.deepcopy(packet)
                changed['agent_action']['task_result']['response_details'][field][key] = value
                self.assertTrue(bound.rule_graph.findings(changed), (field, value))
                with self.assertRaisesRegex(ValueError, '^LEAD_SCHEMA_RULE_REJECTED$'):
                    bound.decode_response(json.dumps(changed))
                self.assertTrue(all(diagnose_json_text(json.dumps(changed), schema) for schema in schemas), (field, value))
            return encoded
        except Exception as failure:
            self._revision_fixture_failure = (type(failure).__name__, str(failure)[:384])
            raise


def task_fixture(test):
    case = LeadOrchestrationTestCase()
    case.addCleanup = test.addCleanup
    case.setUp()
    return case


class BudgetConsultationFixture:
    async def _budget_consultation(self, *, message="Completed budget-bound review.",
                                   pause_before_finalization=False, build_failure=None, infeasible=False):
        from tests.lead_fixtures.preparation_data import fixture_assessment
        assessment = asdict(fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        previous_calls = len(self.backend.calls)
        previous_references = len(self.backend.reference_calls)
        conversation_id = self.conversation("Budget finalization")
        agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)
        aliases = ["user_input", "reference_1", "reference_2", "reference_3"]
        assignments = [{'agent_id': agent, 'instruction': 'Review every supplied input.',
            'input_ids': aliases, 'mode': 'draft' if infeasible else 'review'} for agent in agents]
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, search_history, current_proposal_outcome(assignments,
            domains=('plot_structure', 'character_world', 'tone_execution'),
            purpose='Check the story evidence before recommending a change.',
            activity='draft' if infeasible else 'review', authoring_assessment=assessment, paths=(
            'manuscript/part-01/opening.md',) if infeasible else (),
                                                                         author_review_basis=True), control_plan=selection_plan(
                "consultation.authoring.content" if infeasible else "consultation.authoring.review",
                before_script_response=1))
        proposed = await self.runtime.lead_turn( self.session_id,
            conversation_id, (
                ("Draft from this story. " if infeasible else "Review complete supplied candidate: ")
                + STORY_USER_INPUT + "\n"
                + "${core_documents/global_outline.md} ${core_documents/character_sheets.md} "
                + "${manuscript/part-01/opening.md}"),)
        if infeasible:
            self.assertIs(proposed.kind, LeadOutcomeKind.ANSWER)
            self.assertIs(proposed.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
            self.assertIsNone(proposed.proposal)
            self.assertIsNone(proposed.confirmation_request_id)
            self.assertIsNone(proposed.artifact_proposal)
            self.assertIsNone(proposed.artifact_application_request)
            self.assertIsNone(proposed.story_commit_request)
            self.assertEqual([actor for actor, _ in self.backend.call_records[previous_calls:]], [LEAD_ORCHESTRATOR] * 3)
            history = self.history(conversation_id)
            self.assertEqual(len(submitted_calls(history)), 3)
            self.assertEqual(project_collaboration_history(history).physical_calls_spent, 3)
            self.assertFalse(any(type(event.payload) is CollaborationAssignmentDispatchPayload for event in history))
            self.assertIn("required preparation and complete specialist review", proposed.message)
            return conversation_id, proposed
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        expected = {}
        for agent in agents:
            self.backend.enqueue_for(agent, search_history, lead_specialist_outcome("insufficient_evidence"),)

        def synthesis(call, _provider):
            material = workflow_material(_provider.callback_payload(call))
            input_ids = [item['input_id'] for item in material['inputs']]
            reply_event_ids = [item['reply_event_id'] for item in material['replies']]
            value = {'message': message, 'next_intent': { 'purpose': 'Review every unresolved input again.',
                'assignments': [{ 'agent_id': agent, 'instruction': 'Reconsider the original facts and replies.',
                'input_ids': [*input_ids, *reply_event_ids], 'mode': 'review'} for agent in agents]}}
            for row in value['next_intent']['assignments']:
                row['review_basis'] = {
                    'candidate_input_ids': list(input_ids),
                    'criteria': 'Reconsider the original facts and replies.'}
            expected.update(reply_event_ids=reply_event_ids, unresolved_input_ids=input_ids)
            from tests.provider_wire_fixture import lead_fixture_assignments, lead_fixture_response
            native = wire_result(value, 'consultation-summary-v4')
            follow_up = native['follow_up_consultation']
            rows = follow_up['assignments']
            choices = dict(call.contract.semantic_index.policy.choices)
            self.assertTrue(set(input_ids).issubset(choices['review_candidate_ids']))
            allocated = lead_fixture_assignments(rows, input_choices=choices['allocation_input_ids'],
                    review_choices=choices['review_candidate_ids'], anchors={agent: input_ids[0] for agent in agents},
                recipients={identity: agents[0] for identity in choices['required_input_ids']})
            follow_up.update(allocated)
            response = lead_fixture_response(call, lead_fixture_result(native), family=0,
                branch_key='consultation-summary-v4:family:1')
            self.assertEqual(json.loads(call.contract.decode_response(response).answer_json), value)
            native_synthesis[call.call_id] = (call.contract, json.loads(response))
            return response

        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis)
        from story_writing_agents.agent_context.contracts.bound_call import BoundCallContract
        native_synthesis, checking = {}, set()
        original_final = BoundCallContract.require_authorized_answer
        def retain_authorized_phase(binding, decoded, authorization):
            result = original_final(binding, decoded, authorization)
            identity = binding.offers.call_id
            if identity in native_synthesis and identity not in checking:
                checking.add(identity)
                current, packet = native_synthesis[identity]
                self.assertIs(current, binding)
                self._require_fixture_text_boundaries( binding, packet, authorization=authorization, synthesis=True)
            return result
        with mock.patch.object(BoundCallContract, 'require_authorized_answer', retain_authorized_phase):
            if pause_before_finalization:
                with mock.patch.object(self.runtime.known_result_finalization, 'finalize_known_result',
                                       side_effect=RuntimeError(
                                           "test result persisted before finalization")):
                    with self.assertRaisesRegex(RuntimeError, "test result persisted"):
                        await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
                decision_result = None
            elif build_failure is not None:
                with mock.patch.object(self.runtime.known_result_finalization, 'build_continuation_proposal',
                                       side_effect=build_failure):
                    with self.assertRaises(type(build_failure)) as raised:
                        await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
                    self.assertIs(raised.exception, build_failure)
                decision_result = None
            else:
                decision_result = await self._decide(
                    proposed, CollaborationConfirmationDecision.CONFIRM)
        history = self.history(conversation_id)
        self.assertEqual(len(submitted_calls(history)), 10)
        self.assertEqual(len(self.backend.calls) - previous_calls, 10,)
        self.assertEqual(len(self.backend.reference_calls) - previous_references, 5)
        syntheses = [event for event in history if type(event.payload) is CollaborationLeadSynthesisPayload]
        results = [event for event in history if type(event.payload) is CollaborationExecutionResult]
        self.assertEqual(len(syntheses), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(syntheses[0].payload.message, message)
        self.assertEqual(syntheses[0].payload.evidence, proposed.proposal.basis.evidence)
        self.assertEqual(syntheses[0].payload.reply_event_ids, tuple(expected["reply_event_ids"]))
        self.assertEqual(syntheses[0].payload.unresolved_input_ids, tuple(expected["unresolved_input_ids"]))
        self.assertIsNotNone(syntheses[0].payload.next_intent)
        self.assertEqual(results[0].payload.overall_outcome.value, "succeeded")
        return conversation_id, proposed, decision_result, history, syntheses[0], results[0]
