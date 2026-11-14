'Existing CompactWireDecoderTests scenarios, moved without new cases.'
from __future__ import annotations
import hashlib
import json
import unittest
from dataclasses import replace
from unittest import mock
from story_writing_agents.agent_context.accounting import canonical_provider_json
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextActionError, ContextAnswerAction, ContextReadAction, ContextSearchAction, ContextSearchQuery, ContextSourceHandle
from story_writing_agents.agent_context.manifest import ContextSourceKind
from story_writing_agents.structured_output_validation import diagnose_json_text, failure_issues
from tests.provider_wire_fixture import assert_schema_descriptions
from tests.test_provider_schema_spec import check_schema
from story_writing_agents.agent_context.contracts import action_schema as ctx_action_schema, bound_call as ctx_bound_call, call_offers as ctx_call_offers, context_values as ctx_context_values, errors as ctx_errors, session as ctx_session
from story_writing_agents.agent_context.payload import actions as ctx_actions
from tests.agent_context.invocation import session_fixture as fixture_session_fixture


class CompactWireDecoderTests(unittest.TestCase):
    """Verify the complete strict typed wire-v11 response contract in isolation."""

    def setUp(self) -> None:
        manifest, _snapshots = fixture_session_fixture.manifest_fixture(); self.correlation = fixture_session_fixture.correlation(fixture_session_fixture.binding(manifest), 1)
        self.token = ctx_session.correlation_token(self.correlation)
        self.result_schema = fixture_session_fixture.closed_result_schema({ "kind": {"enum": ["answer"], "type": "string"},
            "message": {"type": "string"}, "proposal": {"type": "null"}, } )
        self.result_contract = ctx_call_offers.ContextJsonObjectResultContract("outcome-v1", self.result_schema, )

    @staticmethod
    def _resolve_handle(value: object) -> ContextSourceHandle:
        if type(value) is not str or not value.startswith("h"):
            return ContextSourceHandle(value)  # type: ignore[arg-type]
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest(); return ContextSourceHandle(f"ctxh_v1_{digest}")

    def decode(self, action: object, *, allowed: tuple[str, ...] = ("search", "read", "answer"),
        contract: ctx_call_offers.ContextJsonObjectResultContract | None = None, source_kinds: tuple[ContextSourceKind, ...] = (
            ctx_context_values.SEARCH_SOURCE_KIND_ORDER ),
    ) -> ContextSearchAction | ContextReadAction | ContextAnswerAction:
        response = (action if type(action) is str else json.dumps(action, ensure_ascii=False, separators=(",", ":")) )
        assert isinstance(response, str)
        return ctx_actions.decode_context_action(response, correlation=self.correlation, allowed_actions=allowed,
            result_contract=contract or self.result_contract,
            allowed_search_source_kinds=source_kinds, resolve_handle=self._resolve_handle, )

    def assert_malformed(self, action: object, *, allowed: tuple[str, ...] = ("search", "read", "answer"),
        contract: ctx_call_offers.ContextJsonObjectResultContract | None = None, source_kinds: tuple[ContextSourceKind, ...] = (
            ctx_context_values.SEARCH_SOURCE_KIND_ORDER ),) -> ctx_errors.ContextExecutionError:
        with self.assertRaises(ctx_errors.ContextExecutionError) as caught:
            self.decode(action, allowed=allowed, contract=contract, source_kinds=source_kinds, )
        self.assertIs(caught.exception.code, ctx_errors.ContextExecutionErrorCode.MALFORMED_ACTION, )
        self.assertIsNone(caught.exception.__cause__); return caught.exception

    def test_schema_is_action_only_and_runtime_correlation_is_exact(self) -> None:
        limits = [4, 2, 2, 4, 8, 8, 16, 200]
        schema = ctx_action_schema.context_action_schema(allowed_actions=("search", "read", "answer"),
            result_contract=self.result_contract, allowed_search_source_kinds=ctx_context_values.SEARCH_SOURCE_KIND_ORDER,
            action_limits=limits, )
        self.assertIs(schema["additionalProperties"], False); self.assertEqual(schema["required"], ["agent_action"])
        self.assertEqual(set(schema["properties"]), {"agent_action"})
        action_variants = schema["properties"]["agent_action"]["anyOf"]
        self.assertEqual( tuple(variant["properties"]["action_type"]["enum"][0] for variant in action_variants),
            ("search_context", "search_context", "read_context", "submit_result"), )
        for variant in action_variants:
            self.assertIs(variant["additionalProperties"], False)
            self.assertEqual(set(variant["required"]), set(variant["properties"]), )
        search, prefixed_search, read, answer = action_variants; assert_schema_descriptions(self, schema)
        self.assertNotEqual(search["description"], prefixed_search["description"])
        self.assertIn("directory", prefixed_search["description"].lower())
        self.assertIn("no artifact-directory restriction", search["description"].lower())
        collapsed = ctx_action_schema.context_action_schema(allowed_actions=("search",), result_contract=self.result_contract,
            allowed_search_source_kinds=(ContextSourceKind.CONVERSATION,), action_limits=limits)
        assert_schema_descriptions(self, collapsed); self.assertNotIn("anyOf", collapsed["properties"]["agent_action"])
        self.assertEqual(collapsed["properties"]["agent_action"]["description"], search["description"])
        self.assertNotIn("artifact_directory_prefix", search["properties"])
        self.assertIn("artifact_directory_prefix", prefixed_search["properties"])
        self.assertEqual(search["properties"]["queries"]["maxItems"], 4)
        query_schema = search["properties"]["queries"]["items"]
        source_kinds_schema = query_schema["properties"]["source_types"]
        self.assertIn("Choose distinct allowed source kinds", source_kinds_schema["description"])
        self.assertEqual(set(source_kinds_schema), {"description", "items", "maxItems", "minItems", "type"}, )
        self.assertNotIn("uniqueItems", source_kinds_schema); self.assertNotIn("prefixItems", source_kinds_schema)
        self.assertIsInstance(source_kinds_schema["items"], dict)
        self.assertEqual(source_kinds_schema["items"]["enum"],
            ["story_document", "conversation_history", "current_consensus_run", "agent_work_history"],)
        self.assertEqual(read["properties"]["read_handles"]["maxItems"], 8)
        self.assertEqual(answer["properties"]["task_result"], self.result_schema)
        self.assertEqual(set(answer["properties"]), {"action_type", "task_result"}); canonical_provider_json(schema)
        self.assertRegex(self.token, r"\A[A-Za-z0-9_-]{43}\Z")
        variants = (replace( self.correlation, scope=replace(self.correlation.scope, session_id="other"), ),
            replace(self.correlation, operation_id="other"), replace(self.correlation, result_correlation_id="other"),
            replace(self.correlation, action_index=2), )
        tokens = (self.token,) + tuple( ctx_session.correlation_token(value) for value in variants )
        self.assertEqual(len(set(tokens)), len(tokens))
        self.assertIsInstance(self.decode( fixture_session_fixture.answer_wire(self.token, {"kind": "answer", "message": "answer",
            "proposal": None}, ), allowed=("answer",), ), ContextAnswerAction, )
        from story_writing_agents.lead_provider_contracts import LeadProviderConstraints, LeadResultRuleSeed
        from story_writing_agents.lead_preparation.call_rule_factory import OrchestrationLeadCallFactory
        from story_writing_agents.structured_output_validation import SchemaRules
        from tests.provider_wire_fixture import finite_fixture_selection

        # A semantic-only unavailable choice must not contaminate SEARCH or READ.
        semantic_schema = {
            "type": "object", "description": "Return one answer without an unavailable choice.",
            "properties": {"response_type": {"type": "string", "enum": ["answer"]},
                           "response_text": {"type": "string"},
                           "unavailable_choice": {"$ref": "#/$defs/unavailable"}},
            "required": ["response_type", "response_text"], "additionalProperties": False,
            "$defs": {"unavailable": {"type": "string", "enum": []}},
        }
        seed = LeadResultRuleSeed.declare("lead-discussion-response-v1", semantic_schema)
        factory = OrchestrationLeadCallFactory(
            LeadProviderConstraints(), seed, "optional-reference", "EN",
            lambda _raw, _authorization: None, lambda _locator: "plot_architect")
        manifest, snapshots = fixture_session_fixture.manifest_fixture()
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, snapshots)
        session = fixture_session_fixture.session(fixture_session_fixture.binding(manifest), registry)
        fixture_session_fixture.mint_handles(session, registry, snapshots)
        offers = session.contract_offers("optional-reference-call", allowed_actions=("search", "read", "answer"))
        bound = ctx_bound_call.BoundCallContract.bind(factory, offers)
        self.assertIs(bound.offers, offers)
        self.assertEqual(len(bound.semantic_index.contracts), 1)

        def schema_nodes(value):
            if type(value) is dict:
                yield value
                for child in value.values():
                    yield from schema_nodes(child)
            elif type(value) is list:
                for child in value:
                    yield from schema_nodes(child)

        semantic = bound.semantic_index.contracts[0]
        self.assertTrue(any(node.get("enum") == [] for node in schema_nodes(semantic.rule_graph.schema_value())))
        actions = {}
        for member in bound.action_index.contracts:
            graph = member.rule_graph.schema_value()
            kind = graph["properties"]["action_type"]["enum"][0]
            if "artifact_directory_prefix" not in graph["properties"]:
                actions[kind] = member
        self.assertEqual(set(actions), {"search_context", "read_context", "submit_result"})
        read_graph = actions["read_context"].rule_graph.schema_value()
        references = [node["$ref"] for node in schema_nodes(read_graph) if "$ref" in node]
        self.assertTrue(references)
        for reference in references:
            target = read_graph
            for name in reference.removeprefix("#/").split("/"):
                target = target[name.replace("~1", "/").replace("~0", "~")]
            self.assertIsInstance(target, dict)
        result = {"schema_id": semantic.schema_id, "response_type": "answer", "response_text": "Visible.",
                  "response_details": {}}
        for kind, fields in (
            ("search_context", {"queries": [{"search_text": "needle", "source_types": {"story_document": True}}]}),
            ("read_context", {"read_handles": finite_fixture_selection(offers.issued_handle_aliases,
                                                                       (offers.issued_handle_aliases[0],))}),
            ("submit_result", {"task_result": result}),
        ):
            packet = {"agent_action": {"schema_id": actions[kind].schema_id, "action_type": kind, **fields}}
            self.assertEqual(bound.rule_graph.findings(packet), ())
            self.assertTrue(bound.decode_response(json.dumps(packet)).belongs_to(bound))
        projection = bound.rule_graph.provider_projection()
        self.assertFalse(any(node.get("enum") == [] for node in schema_nodes(projection.schema_value())))
        self.assertEqual(projection.findings(packet), ())
        result["response_details"]["unavailable_choice"] = "not-offered"
        self.assertTrue(bound.rule_graph.findings(packet))
        self.assertTrue(projection.findings(packet))
        with self.assertRaises(ValueError):
            bound.decode_response(json.dumps(packet))
        invalid = json.loads(json.dumps(semantic_schema))
        invalid["$defs"]["unvisited_invalid"] = {"type": "string", "enum": []}
        with self.assertRaises(ValueError):
            SchemaRules.declare(invalid)


        from types import SimpleNamespace
        from story_writing_agents.conversations import LeadProposalRelation
        from story_writing_agents.lead_schema_groups import (
            LeadGroupRequestBasis, LeadGroupPhase, SchemaGroupControlOwner, SchemaGroupSelection,
            SchemaGroupReselection)
        from story_writing_agents.lead_preparation.response_rules import lead_front_result_contract, lead_revision_result_contract
        from story_writing_agents.lead_provider_contracts import LeadDiscussionConstraints
        from story_writing_agents.specialist_routing import AuthoringScope, AuthoringActivity
        from story_writing_agents.structured_output_validation import BoundContract
        from tests.provider_wire_fixture import lead_fixture_response, lead_fixture_scope_record
        scope = AuthoringScope(AuthoringActivity.WORKFLOW, (), (), "Explain workflow.", "group-task", 1, schema_version=3)
        facts = LeadProviderConstraints(scope=scope, task_revisions=(("group-task", 1),), input_ids=("user_input",))
        seed = lead_front_result_contract(LeadProposalRelation.NEW_STEP, facts.input_ids, constraints=facts)
        denied = []
        def deny_business(raw, authorization):
            denied.append(raw)
            raise ValueError("business authority denied")
        grouped_factory = OrchestrationLeadCallFactory(facts, seed, "lead.front", "EN", deny_business,
            lambda _locator: "plot_architect", schema_group_protocol=True)
        basis = LeadGroupRequestBasis("group-operation", "group-result", "group-scope")
        selector = grouped_factory.for_offer(offers, request_basis=basis)
        from story_writing_agents.provider_schema_selection import SchemaSelectionInput
        from story_writing_agents.provider_contract_guidance import add_contract_guidance
        wire = SchemaSelectionInput("select", offers.call_id, selector.outer_inner_schema_ids,
            2, "current-context", catalog=json.dumps(selector.group_stage.catalog.as_object()).encode()).as_object()
        self.assertEqual(SchemaSelectionInput.from_value(wire).as_object(), wire)
        self.assertEqual(wire["issued_contract"]["contracts"], [
            {"outer_schema_id": outer, "result_schema_id": inner}
            for outer, inner in selector.outer_inner_schema_ids])
        self.assertEqual(wire["context_delivery"], {"mode": "bootstrap", "context_ref": "current-context"})
        invalid_wire = []
        for path, value in ((('extra',), True), (('remaining_provider_calls',), True),
                (('phase',), 'generate'), (('catalog_id',), 'duplicate-catalog'),
                (('issued_contract', 'contracts'), []), (('issued_contract', 'extra'), None),
                (('context_delivery', 'mode'), 'unknown'), (('context_delivery', 'base_context_ref'), 'undeclared-base'),
                (('active_selection',), {'origin_catalog_id': 'foreign', 'group_id': 'answer.workflow'})):
            changed = json.loads(json.dumps(wire))
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            invalid_wire.append(changed)
        missing_catalog = json.loads(json.dumps(wire))
        del missing_catalog['catalog']
        invalid_wire.append(missing_catalog)
        for changed in invalid_wire:
            self.assertRaises((TypeError, ValueError), SchemaSelectionInput.from_value, changed)
            self.assertRaises((TypeError, ValueError), add_contract_guidance,
                {'response_contract': {'schema_selection': changed}}, selector.schema_value())
        pure_missing = grouped_factory.for_offer(replace(offers, call_id="pure-missing-R"), request_basis=basis)
        self.assertIsNone(pure_missing.offers.remaining_provider_calls)
        self.assertEqual(check_schema(pure_missing.schema_value()), ())
        for current in (pure_missing, grouped_factory.for_offer(
                replace(offers, call_id="physical-current-R", remaining_provider_calls=2), request_basis=basis)):
            declaration = SchemaSelectionInput("select", current.offers.call_id, current.outer_inner_schema_ids,
                2, "complete-context", catalog=json.dumps(current.group_stage.catalog.as_object()).encode()).as_object()
            complete = add_contract_guidance({"request_correlation_token": self.token,
                "task_input": {"workflow_type": "lead_front_channel", "user_request_text": "Explain workflow."},
                "response_contract": {"schema_selection": declaration}}, current.schema_value())
            parsed = SchemaSelectionInput.from_value(complete["response_contract"]["schema_selection"])
            self.assertEqual((parsed.phase, parsed.call_id, parsed.contracts),
                ("select", current.offers.call_id, current.outer_inner_schema_ids))
            self.assertEqual(json.loads(parsed.catalog), current.group_stage.catalog.as_object())
            self.assertEqual(complete["contract_guidance"]["response_rules"].count(
                "현재 단계는 Phase 1(그룹 선택)입니다. Phase 2에서는 선택한 그룹의 상세 계약에 따라 실제 답변을 작성합니다."), 1)
            current.require_projection(current.schema_value())
            if current is pure_missing:
                with self.assertRaisesRegex(ValueError, "schema_selection does not match the exact current bound call"):
                    current.require_submission(complete, current.schema_value())
            else:
                self.assertIsNone(current.require_submission(complete, current.schema_value()))
        def control_frame(binding, group):
            member, = (item for item in binding.semantic_index.contracts
                       if type(item.owner) is SchemaGroupControlOwner)
            outer, = (outer for outer, inner in binding.outer_inner_schema_ids if inner == member.schema_id)
            return {"agent_action": {"schema_id": outer, "action_type": "submit_result", "task_result": {
                "schema_id": member.schema_id, "response_type": member.rule_graph.literal_at(("response_type",)),
                "catalog_id": binding.group_stage.catalog.catalog_id, "group_id": group, "selection_reason": ""}}}
        packet = control_frame(selector, "answer.workflow")
        self.assertEqual(check_schema(selector.schema_value()), ())
        decoded = selector.decode_value(packet)
        self.assertIs(type(decoded.group_control), SchemaGroupSelection)
        self.assertRaises(TypeError, lambda: decoded.answer_json)
        self.assertRaises(TypeError, selector.require_authorized_answer, decoded, None)
        for field in packet["agent_action"]["task_result"]:
            missing = json.loads(json.dumps(packet)); del missing["agent_action"]["task_result"][field]
            self.assertRaises(ValueError, selector.decode_value, missing)
        for field, wrong in (("schema_id", "old-inner"), ("response_type", "schema_group_reselection"),
                ("catalog_id", "old-catalog"), ("group_id", "f0"), ("selection_reason", None),
                ("selection_reason", 1), ("selection_reason", {}), ("foreign", "extra")):
            invalid = json.loads(json.dumps(packet)); invalid["agent_action"]["task_result"][field] = wrong
            self.assertRaises(ValueError, selector.decode_value, invalid)
        for path in ((), ("agent_action",)):
            invalid = json.loads(json.dumps(packet)); node = invalid
            for name in path: node = node[name]
            node["foreign"] = True
            self.assertRaises(ValueError, selector.decode_value, invalid)
        duplicate = json.dumps(packet).replace('"selection_reason": ""', '"selection_reason": "", "selection_reason": ""')
        self.assertRaises(ValueError, selector.decode_response, duplicate)
        old_outer = json.loads(json.dumps(packet)); old_outer["agent_action"]["schema_id"] = "old-outer"
        self.assertRaises(ValueError, selector.decode_value, old_outer)
        record = selector.record_group_control(decoded)
        workflow = selector.group_workflow.accept(record, remaining_calls=1)
        generation_offers = replace(offers, call_id="group-generation", action_index=offers.action_index + 1)
        generated = grouped_factory.for_offer(generation_offers, request_basis=basis, group_workflow=workflow,
                                             prepared_compilation=selector.prepared_compilation)
        self.assertEqual(check_schema(generated.schema_value()), ())
        self.assertEqual(set(generated.schema_value()['properties']), {'agent_action'})
        self.assertIs(generated.prepared_compilation, selector.prepared_compilation)
        reselect = control_frame(generated, "question.workflow")
        self.assertIs(type(generated.decode_value(reselect).group_control), SchemaGroupReselection)
        initial_repeat = control_frame(generated, "question.workflow")
        initial_repeat["agent_action"]["task_result"]["response_type"] = "schema_group_selection"
        self.assertRaises(ValueError, generated.decode_value, initial_repeat)
        reselect["agent_action"]["task_result"]["group_id"] = "answer.workflow"
        self.assertRaises(ValueError, generated.decode_value, reselect)
        self.assertRaises(TypeError, generated.record_group_control, decoded)
        self.assertEqual(denied, [])
        body = {"response_type": "answer", "response_text": "Workflow only.", "response_details": {
            "task_scope": lead_fixture_scope_record({"interpretation": "Inspect the declared synthetic scope.", "activity": "workflow", "intent": "authoring", "domains": [],
                "target_paths": [], "contributions": [], "dependency_slots": [], "task_id": "group-task",
                "task_revision": 1}, fixed_targets=True, fixed_dependencies=True)}}
        answer_text = lead_fixture_response(SimpleNamespace(contract=generated), body, family=0)
        answer = generated.decode_response(answer_text)
        false_completion = json.loads(answer_text)
        false_completion["agent_action"]["schema_id"] = packet["agent_action"]["schema_id"]
        false_completion["agent_action"]["task_result"]["schema_id"] = packet["agent_action"]["task_result"]["schema_id"]
        false_completion["agent_action"]["task_result"]["response_text"] = "업무를 완료했습니다."
        self.assertRaises(ValueError, selector.decode_value, false_completion)
        self.assertEqual(denied, [])
        binding = generated.generation_binding()
        self.assertEqual(binding.schema_id_pairs, tuple((outer, inner) for outer, inner in
            generated.outer_inner_schema_ids if inner == answer.selected_result.schema_id))
        self.assertIs(workflow.complete(binding, answer.selected_result).phase, LeadGroupPhase.DONE)
        self.assertRaises(ValueError, workflow.complete, binding, decoded.selected_result)
        outer, inner = binding.member_bindings[0]
        shadow = BoundContract.bind(inner.owner, inner.identity)
        shadow_result = shadow.decode_selected(json.loads(lead_fixture_response(
            SimpleNamespace(contract=generated), body, family=0))["agent_action"]["task_result"])
        self.assertRaises(ValueError, binding.require_result, shadow_result)
        for changes in ({"member_bindings": ()}, {"member_bindings": binding.member_bindings * 2},
                {"member_bindings": ((selector.action_index.contracts[0], inner),)},
                {"provided_contracts": (inner,)}, {"provided_call_id": "foreign-call"},
                {"provided_offer_digest": "0" * 64}, {"logical_origin_call_id": "foreign-origin"},
                {"current_offer": b"{}"}, {"eligibility_offer": b"{}"}):
            self.assertRaises((TypeError, ValueError), replace, binding, **changes)
        for retained, kind in ((selector, "select"), (generated, "generate")):
            repaired_offers = replace(retained.offers, call_id="group-repair-" + kind,
                                      action_index=retained.offers.action_index + 1)
            repaired = grouped_factory.for_offer(repaired_offers, retained.repair_lineage())
            self.assertIs(repaired.semantic_index, retained.semantic_index)
            self.assertIs(repaired.group_workflow, retained.group_workflow)
            self.assertEqual(repaired.outer_inner_schema_ids, retained.outer_inner_schema_ids)
            replayed = grouped_factory.for_offer(replace(repaired_offers, call_id="group-replay-" + kind,
                action_index=repaired_offers.action_index + 1), repaired.replay_lineage())
            self.assertIs(replayed.group_stage.catalog, retained.group_stage.catalog)
            self.assertNotEqual(replayed.outer_inner_schema_ids, retained.outer_inner_schema_ids)
            if kind == "generate":
                repair_binding = repaired.generation_binding()
                self.assertEqual(repair_binding.provided_call_id, generated.offers.call_id)
                repair_binding.require_result(answer.selected_result)
                self.assertRaises(ValueError, replayed.generation_binding().require_result, answer.selected_result)
                replayed_result = replayed.decode_response(lead_fixture_response(SimpleNamespace(contract=replayed), body, family=0))
                replayed.generation_binding().require_result(replayed_result.selected_result)
            self.assertRaises(ValueError, replayed.decode_value, control_frame(retained, "question.workflow"))
            self.assertRaises(ValueError, grouped_factory.for_offer, repaired_offers, retained.repair_lineage(),
                              request_basis=replace(basis, scope_ref="changed-authority"))
        control_owner = selector.semantic_index.contracts[0].owner
        for field, changed in (("owner_revision", "foreign"), ("_rules", replace(control_owner.declare_rules())),
                               ("_decoder", object())):
            original = getattr(control_owner, field)
            try:
                object.__setattr__(control_owner, field, changed)
                self.assertRaises(ValueError, selector.require_current_offer, offers)
            finally: object.__setattr__(control_owner, field, original)
        with mock.patch.object(type(grouped_factory), "declare_schema_groups", lambda *_args, **_kwargs: None):
            self.assertRaises(ValueError, selector.require_current_offer, offers)
        authority = session.authorize_answer(ContextAnswerAction(
            fixture_session_fixture.correlation(session.binding, session.next_action_index), answer.answer_json, ()))
        self.assertRaises(ValueError, generated.require_authorized_answer, answer, authority)
        self.assertEqual(denied, [answer.answer_json])
        revision_seed = lead_revision_result_contract(LeadProposalRelation.NEW_STEP, facts.input_ids,
            facts.task_revisions, facts, LeadDiscussionConstraints("group-discussion", 1))
        ordinary = replace(grouped_factory, schema_group_protocol=False, rule_seed=revision_seed).for_offer(offers)
        self.assertIsNone(ordinary.schema_group_phase)
        self.assertRaises(ValueError, replace, grouped_factory, rule_seed=revision_seed)

        from story_writing_agents.agent_context.accounting import (
            ContextComponentBytes, ContextComponentKind,  provider_schema_statistics,
        )
        from story_writing_agents.agent_context.group_reporting import (
            LeadGroupRequestMeasurement, group_request_size_report,
        )
        from story_writing_agents.provider_adapter import ProviderInvocation
        comparison_basis = canonical_provider_json({
            "operation_id": basis.operation_id, "scope_ref": basis.scope_ref,
            "catalog_id": selector.group_stage.catalog.catalog_id,
            "profile": "same-explicit-workflow-fixture",
        })
        catalogue = selector.group_stage.catalog
        full = replace(grouped_factory, schema_group_protocol=False).for_offer(offers)
        prepared = []
        exported = []
        variants = [("full", full, None), ("select", selector, None)]
        for group_id in catalogue.group_ids:
            selected_control = selector.decode_value(control_frame(selector, group_id))
            selected_record = selector.record_group_control(selected_control)
            selected_workflow = selector.group_workflow.accept(selected_record, remaining_calls=1)
            member = grouped_factory.for_offer(
                generation_offers, request_basis=basis, group_workflow=selected_workflow,
                prepared_compilation=selector.prepared_compilation)
            variants.append(("generate", member, group_id))
        for variant, current_bound, group_id in variants:
            schema = current_bound.schema_value()
            self.assertEqual(check_schema(schema), ())
            if variant == "full":
                payload = {}
            else:
                current_selection = SchemaSelectionInput(
                    variant, current_bound.offers.call_id, current_bound.outer_inner_schema_ids,
                    2, "comparison-context", catalog=canonical_provider_json(catalogue.as_object()),
                    active_selection=None if group_id is None else (catalogue.catalog_id, group_id))
                payload = {"response_contract": {"schema_selection": current_selection.as_object()}}
                payload = add_contract_guidance(payload, schema)
            invocation = ProviderInvocation(
                "comparison-session", schema, payload,
                thread_id=None, binding_components=(ContextComponentBytes(ContextComponentKind.ROLE_SYSTEM_PROMPT, 17),))
            prepared.append(LeadGroupRequestMeasurement(variant, comparison_basis, invocation, group_id))
            exported.append((variant, group_id, invocation))
        comparison = group_request_size_report(tuple(prepared), group_ids=catalogue.group_ids)
        self.assertEqual(comparison["comparison_basis_sha256"], hashlib.sha256(comparison_basis).hexdigest())
        self.assertEqual(len(comparison["measurements"]), len(catalogue.group_ids) + 2)
        self.assertEqual({row["group_id"] for row in comparison["measurements"] if row["variant"] == "generate"},
                         set(catalogue.group_ids))
        for row, (variant, group_id, invocation) in zip(comparison["measurements"], exported, strict=True):
            native_schema = canonical_provider_json(invocation.schema)
            native_payload = invocation.payload_json
            self.assertEqual((row["variant"], row["group_id"]), (variant, group_id))
            self.assertEqual(row["schema_utf8_bytes"], len(native_schema))
            self.assertEqual(row["payload_utf8_bytes"], len(native_payload))
            if variant == "full":
                self.assertIsNone(row["outer_contract_linkage_utf8_bytes"])
            else:
                wire_selection = json.loads(native_payload)["response_contract"]["schema_selection"]
                linkage = {"issued_contract": wire_selection["issued_contract"]}
                if variant == "generate":
                    linkage["active_selection"] = wire_selection["active_selection"]
                if "catalog_id" in wire_selection:
                    linkage["catalog_id"] = wire_selection["catalog_id"]
                self.assertEqual(row["outer_contract_linkage_utf8_bytes"],
                                 len(canonical_provider_json(linkage)))
            self.assertEqual(row["binding_utf8_bytes"], 17)
            self.assertEqual(row["prepared_model_input_utf8_bytes"],
                             len(native_schema) + len(native_payload) + 17)
            self.assertFalse(row["submitted"])
            self.assertIn("prepared", row["measurement_stage"])
        generated_sizes = [row for row in comparison["measurements"] if row["variant"] == "generate"]
        self.assertEqual(comparison["largest_generate_schema"]["schema_utf8_bytes"],
                         max(row["schema_utf8_bytes"] for row in generated_sizes))
        self.assertEqual(comparison["largest_generate_request"]["prepared_model_input_utf8_bytes"],
                         max(row["prepared_model_input_utf8_bytes"] for row in generated_sizes))
        with self.assertRaises(ValueError):
            group_request_size_report(tuple(prepared[:-1]), group_ids=catalogue.group_ids)
        with self.assertRaises(ValueError):
            group_request_size_report(tuple(prepared) + (prepared[-1],), group_ids=catalogue.group_ids)
        foreign = replace(prepared[0], comparison_basis=canonical_provider_json({"profile": "different"}))
        with self.assertRaises(ValueError):
            group_request_size_report((foreign,) + tuple(prepared[1:]), group_ids=catalogue.group_ids)
        census = provider_schema_statistics({
            "type": "object", "properties": {"agent_action": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "$defs": {"One": {"type": "string", "enum": ["한", "🙂"]}},
        })
        self.assertEqual(census.outer_alternatives, 2)
        self.assertEqual(census.any_of_paths, ("$/properties/agent_action/anyOf",))
        self.assertEqual((census.definition_count, census.enum_value_count), (1, 2))
        self.assertEqual(denied, [answer.answer_json])

    def test_valid_search_read_and_natural_result_shapes_decode_exactly(self) -> None:
        search = self.decode(fixture_session_fixture.search_wire( self.token, [ ("Needle", ["writing_artifact", "conversation",
            "active_debate", "own_agent_state", ], ) ], ), allowed=("search", "answer"), )
        self.assertIsInstance(search, ContextSearchAction)
        assert isinstance(search, ContextSearchAction)
        self.assertEqual(search.queries[0].normalized_text, "needle")
        self.assertEqual(search.queries[0].source_kinds, ctx_context_values.SEARCH_SOURCE_KIND_ORDER, )

        read = self.decode(fixture_session_fixture.read_wire(self.token, ["h1", "h2"]), allowed=("read", "answer"), )
        self.assertIsInstance(read, ContextReadAction)
        assert isinstance(read, ContextReadAction)
        answer_handle = read.handles[0]

        result = {"proposal": None, "message": "Visible.", "kind": "answer"}
        structured = self.decode(fixture_session_fixture.answer_wire(self.token, result, used_handles=("h1",)), allowed=("answer",), )
        self.assertIsInstance(structured, ContextAnswerAction)
        assert isinstance(structured, ContextAnswerAction)
        self.assertEqual(structured.answer, '{"kind":"answer","message":"Visible.","proposal":null}', )
        self.assertEqual(structured.used_handles, ())
        for invalid in ([], "search_text", 1, None, True):
            with self.subTest(json_object_invalid=invalid):
                self.assert_malformed(fixture_session_fixture.answer_wire(self.token, invalid), allowed=("answer",), )

    def test_search_wire_supports_shared_artifact_prefix_and_kind_matrix(self) -> None:
        def schema(kinds=ctx_context_values.SEARCH_SOURCE_KIND_ORDER):
            return ctx_action_schema.context_action_schema(allowed_actions=("search",),
                result_contract=self.result_contract, allowed_search_source_kinds=kinds,
                action_limits=[4, 2, 2, 4, 8, 8, 16, 200])
        wire = fixture_session_fixture.search_wire(self.token, [("First", ["writing_artifact"]),
            ("Second", ["conversation", "active_debate", "own_agent_state"])])
        self.assertEqual(diagnose_json_text(json.dumps(wire), schema()), ())
        unprefixed = self.decode(wire, allowed=("search",)); self.assertIsInstance(unprefixed, ContextSearchAction)
        self.assertEqual(tuple((q.normalized_text, q.source_kinds, q.artifact_path_prefix) for q in unprefixed.queries),
            (("first", (ContextSourceKind.WRITING_ARTIFACT,), None), ("second", (ContextSourceKind.CONVERSATION,
                ContextSourceKind.ACTIVE_DEBATE, ContextSourceKind.OWN_AGENT_STATE), None)))
        prefix = "manuscript/part-01/"
        wire = fixture_session_fixture.search_wire(self.token, [("Alpha", ["writing_artifact"]), ("Beta", ["writing_artifact"])], path_prefix=prefix)
        self.assertEqual(diagnose_json_text(json.dumps(wire), schema()), ())
        prefixed = self.decode(wire, allowed=("search",)); self.assertIsInstance(prefixed, ContextSearchAction)
        self.assertEqual(tuple(q.artifact_path_prefix for q in prefixed.queries), (prefix, prefix))
        self.assertEqual(tuple(q.normalized_text for q in prefixed.queries), ("alpha", "beta"))
        for kinds in (["conversation"], ["writing_artifact", "conversation"]):
            invalid = fixture_session_fixture.search_wire(self.token, [("Alpha", kinds)], path_prefix=prefix)
            self.assertTrue(diagnose_json_text(json.dumps(invalid), schema()))
            self.assert_malformed(invalid, allowed=("search",))
        conversation_schema = schema((ContextSourceKind.CONVERSATION,))
        self.assertNotIn("artifact_directory_prefix", repr(conversation_schema))
        self.assertTrue(diagnose_json_text(json.dumps(wire), conversation_schema))
        canonical_pair = (ContextSourceKind.WRITING_ARTIFACT, ContextSourceKind.CONVERSATION)
        for provider_order in (["writing_artifact", "conversation"], ["conversation", "writing_artifact"]):
            with self.subTest(provider_order=provider_order):
                pair = self.decode(fixture_session_fixture.search_wire(self.token, [("pair", provider_order)]),
                                   allowed=("search",), source_kinds=canonical_pair)
                self.assertIsInstance(pair, ContextSearchAction)
                self.assertEqual(pair.queries[0].source_kinds, canonical_pair)
        for kind in ctx_context_values.SEARCH_SOURCE_KIND_ORDER:
            with self.subTest(kind=kind):
                action = self.decode(fixture_session_fixture.search_wire(self.token, [("kind", [kind.value])]),
                                     allowed=("search",), source_kinds=(kind,))
                self.assertIsInstance(action, ContextSearchAction)
                self.assertEqual(action.queries[0].source_kinds, (kind,))

    def test_artifact_prefix_validation_is_exact_and_one_scope_per_search(self) -> None:
        valid = ("core_documents/", "manuscript/", "manuscript/part-01/",
            "manuscript/part-01/scene.md/", "manuscript/space dir/",
            "manuscript/.hidden%25/", "manuscript/private-\ue000/", "manuscript/unassigned-\u0378/", )
        for prefix in valid:
            with self.subTest(valid=prefix):
                query = ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,), prefix, )
                self.assertEqual(query.artifact_path_prefix, prefix)

        invalid = ("", "/manuscript/", "manuscript", "manuscript\\part-01/", "manuscript//part-01/",
            "manuscript/./", "manuscript/../", "discussion_logs/", "core_documents/nested/",
            "manuscript/part-01/chapter.md", "manuscript/}/", "manuscript/\u2028/", "manuscript/\u2029/",
            "manuscript/new\nline/", "manuscript/joiner-\u200d/", "manuscript/surrogate-\ud800/", )
        for prefix in invalid:
            with (self.subTest(invalid=repr(prefix)), self.assertRaises(ContextActionError),):
                ContextSearchQuery( "needle", (ContextSourceKind.WRITING_ARTIFACT,), prefix, )

        with self.assertRaises(ContextActionError):
            ContextSearchQuery("needle", (ContextSourceKind.CONVERSATION,), "manuscript/", )
        with self.assertRaises(ContextActionError):
            ContextSearchAction(self.correlation, ( ContextSearchQuery("one",
                        (ContextSourceKind.WRITING_ARTIFACT,), "manuscript/part-01/", ), ContextSearchQuery("two",
                        (ContextSourceKind.WRITING_ARTIFACT,), "manuscript/part-02/", ), ), )

        from story_writing_agents.agent_context.actions import (
            artifact_directory_prefix_rule, search_text_rule)
        from story_writing_agents.structured_output_validation import SchemaRules
        prefix_rules = SchemaRules.declare(artifact_directory_prefix_rule())
        for prefix in valid + ("manuscript/.../", "manuscript/..name/"):
            self.assertEqual(prefix_rules.findings(prefix), (), repr(prefix))
        for prefix in invalid + ("manuscript/a/\n",):
            self.assertTrue(prefix_rules.findings(prefix), repr(prefix))
        query_rules = SchemaRules.declare(search_text_rule(200))
        for text, normalized in (("needle", "needle"), ("포기😀", "포기😀"),
                ("A\u2028B", "a b"), (" \u00a0ﬃß ", "ffiss"), ("", None), (" ", None),
                ("\u00a0", None), ("x\x00", None), ("x\n", None), ("x\u200d", None),
                ("\ue000", None), ("\u0378", None), ("\ud800", None)):
            self.assertEqual(not query_rules.findings(text), normalized is not None, repr(text))
            if normalized is None:
                with self.assertRaises(ContextActionError):
                    ContextSearchQuery(text, (ContextSourceKind.WRITING_ARTIFACT,))
            else:
                query = ContextSearchQuery(text, (ContextSourceKind.WRITING_ARTIFACT,))
                self.assertEqual(query.normalized_text, normalized)
        for text, normalized in (("ﬃ" * 66 + "ff", "ffi" * 66 + "ff"),
                                  ("ß" * 100, "ss" * 100)):
            query = ContextSearchQuery(text, (ContextSourceKind.WRITING_ARTIFACT,))
            self.assertEqual(query.normalized_text, normalized)
        for text in ("ﬃ" * 67, "ß" * 101):
            self.assertEqual(query_rules.findings(text), ())
            with self.assertRaises(ContextActionError):
                ContextSearchQuery(text, (ContextSourceKind.WRITING_ARTIFACT,))

    def test_malformed_exact_shape_types_routes_duplicates_and_limits_reject(self) -> None:
        base = fixture_session_fixture.answer_wire(self.token, {"kind": "answer", "message": "Visible.", "proposal": None})
        answer_action = base["agent_action"]
        malformed = ({}, {**base, "extra": 1}, {key: value for key, value in base.items() if key != "agent_action"},
            {**base, "correlation": "A" * 43}, {**base, "correlation": True}, {**base, 'agent_action': True},
            {**base, 'agent_action': {**answer_action, 'action_type': "unknown"}},
            {**base, 'agent_action': {**answer_action, "used_handles": True}},
            {**base, 'agent_action': {**answer_action, "extra": 1}}, {'agent_action': {'action_type': 'submit_result'}},
            '{"agent_action":%s,"agent_action":%s}' % tuple(json.dumps(answer_action, separators=(",", ":")) for _ in range(2)))
        for index, value in enumerate(malformed):
            with self.subTest(value=value):
                if index in (1, 3, 4, 7, 8):
                    self.assertEqual(self.decode(value).answer, '{"kind":"answer","message":"Visible.","proposal":null}')
                else: self.assert_malformed(value)
        self.assert_malformed(base, allowed=("search",))
        self.assert_malformed(fixture_session_fixture.search_wire(self.token, [("x", ["conversation"])]),
                              source_kinds=(ContextSourceKind.WRITING_ARTIFACT,))
        self.assert_malformed(fixture_session_fixture.search_wire(self.token, [("x", ["writing_artifact"]), ("Ｘ", ["writing_artifact"])]))
        distinct = self.decode(fixture_session_fixture.search_wire(self.token, [("x", ["writing_artifact"]), ("Ｘ", ["conversation"])]))
        self.assertEqual(len(distinct.queries), 2)
        for path_prefix, queries, path, rule in ( (None, [], "queries", "expected a nonempty array; got array"),
            (None, True, "queries", "expected a nonempty array of search query objects"),
            (None, [{"search_text": "x"}], "queries[0]", "expected object containing exactly"),
            (None, [{"search_text": "x", "source_types": []}], "queries[0].source_types", "expected at least one source kind"),
            (None, [{"search_text": "x", "source_types": ["conversation_history", "conversation_history"]}], "queries[0].source_types", "source kinds must be unique"),
            (None, [{"search_text": "x", "source_types": ["unknown"]}], "queries[0].source_types[0]", "expected an authorized source type:"),
            (None, [{"search_text": "x", "source_types": ["story_document"], "extra": True}], "queries[0]", "expected object containing exactly"),
            ("manuscript/part-01/chapter.md", [{"search_text": "x", "source_types": ["story_document"]}], "artifact_directory_prefix", "relative POSIX prefix ending in /"),
            ("manuscript/", [{"search_text": "x", "source_types": ["conversation_history"]}], "artifact_directory_prefix", "artifact_directory_prefix requires source_types to contain only story_document"),
            ("manuscript/", [{"search_text": "x", "source_types": ["conversation_history", "story_document"]}], "artifact_directory_prefix", "artifact_directory_prefix requires source_types to contain only story_document"),
        ):
            action = {"action_type": "search_context", "artifact_directory_prefix": path_prefix, "queries": queries}
            with self.subTest(search_action=action):
                if queries == [{'search_text': 'x', 'source_types': ['story_document'], 'extra': True}]:
                    accepted = self.decode({'agent_action': action}, allowed=('search',))
                    self.assertEqual(tuple((q.normalized_text, q.source_kinds) for q in accepted.queries),
                                     (('x', (ContextSourceKind.WRITING_ARTIFACT,)),))
                    continue
                failure = self.assert_malformed({'agent_action': action})
                issues = tuple(issue.removeprefix("Structured reply validation\n") for issue in
                               ctx_actions.context_validation_details(json.dumps({"agent_action": action}), (), failure))
                self.assertEqual(len(issues), 1)
                self.assertTrue(issues[0].startswith(f"$.agent_action.{path}: "), issues)
                self.assertIn(rule, issues[0])
                if "object containing exactly" in rule:
                    self.assertIn("search_text", issues[0]); self.assertIn("source_types", issues[0])
        self.assert_malformed(fixture_session_fixture.search_wire(self.token, [("x", ["writing_artifact"])]),
            source_kinds=(ContextSourceKind.CONVERSATION,), )
        self.assert_malformed(fixture_session_fixture.read_wire(self.token, ["h1", "h1"])); read_with_extra = fixture_session_fixture.read_wire(self.token, ["h1"])
        read_with_extra["agent_action"]["extra"] = True
        self.assertEqual(self.decode(read_with_extra).handles, (self._resolve_handle('h1'),))
        self.assert_malformed(fixture_session_fixture.search_wire(self.token, [(f"q{i}", ["writing_artifact"])
            for i in range(CONTEXT_ACTION_POLICY.max_queries_per_search + 1)]))
        self.assert_malformed(fixture_session_fixture.read_wire(self.token, [f"h{i}" for i in range(CONTEXT_ACTION_POLICY.max_handles_per_read + 1)]))

    def test_invalid_read_values_report_only_fixed_source_handle_guidance(self) -> None:
        private_values = ("conflict", "PRIVATE/critique text")
        with self.assertRaises(ctx_errors.ContextExecutionError) as caught:
            self.decode(fixture_session_fixture.read_wire(self.token, list(private_values)))

        failure = caught.exception
        self.assertIs( failure.code, ctx_errors.ContextExecutionErrorCode.MALFORMED_ACTION, )
        self.assertEqual(str(failure), "contextual provider read action is invalid: `handles` must contain "
            "source handles returned by the preceding context search", )
        self.assertIsNone(failure.__cause__)
        rendered = "\n".join( ( str(failure), repr(failure), repr(failure.__cause__), repr(failure.__context__), ) )
        for private in private_values: self.assertNotIn(private, rendered)

    def test_provider_correlation_echo_is_rejected_as_an_extra_field(self, ) -> None:
        actual_correlation = "PRIVATE critique prose\x1b]unsafe\x07"
        with self.assertRaises(ctx_errors.ContextExecutionError) as caught:
            value = fixture_session_fixture.read_wire(actual_correlation, []); value["correlation"] = actual_correlation
            self.decode(value)

        failure = caught.exception
        self.assertIs( failure.code, ctx_errors.ContextExecutionErrorCode.MALFORMED_ACTION, )
        self.assertEqual(str(failure), "contextual provider read action is invalid: `handles` must contain "
                         "source handles returned by the preceding context search"); self.assertIsNone(failure.__cause__)
        rendered = "\n".join( ( str(failure), repr(failure), repr(failure.__cause__), repr(failure.__context__), ) )
        for private in (self.token, actual_correlation, "PRIVATE critique prose", "\x1b", "\x07", ):
            self.assertNotIn(private, rendered)
        value['agent_action']['read_handles'] = ['h1']
        accepted = self.decode(value)
        self.assertEqual(accepted.handles, (self._resolve_handle('h1'),))
        self.assertEqual(accepted.correlation, self.correlation)
        self.assertNotIn(actual_correlation, repr(accepted))

    def test_unrelated_malformed_actions_keep_generic_diagnostic(self) -> None:
        cases = ("{", { "correlation": self.token, 'agent_action': {'action_type': 'submit_result',
            "used_handles": []}, }, { "correlation": self.token, 'agent_action': { 'action_type': 'read_context',
            'read_handles': ["h1"], "used_handles": ["h1"], }, }, )
        for index, value in enumerate(cases):
            with self.subTest(value=value):
                if index == 2:
                    accepted = self.decode(value)
                    self.assertEqual(accepted.handles, (self._resolve_handle('h1'),))
                    self.assertEqual(accepted.correlation, self.correlation)
                else:
                    with self.assertRaises(ctx_errors.ContextExecutionError) as caught: self.decode(value)
                    self.assertIs(caught.exception.code, ctx_errors.ContextExecutionErrorCode.MALFORMED_ACTION)
                    self.assertEqual(str(caught.exception), "contextual provider action is malformed")
                    self.assertIsNone(caught.exception.__cause__)

    def test_double_encoded_or_invalid_structured_results_reject(self) -> None:
        valid = {"kind": "answer", "message": "Visible.", "proposal": None}
        cases = (json.dumps(valid, separators=(",", ":")), {"kind": "answer", "message": "Visible."},
                 {**valid, "extra": True}, {**valid, "kind": "unknown"}, [], {**valid, "message": "\ud800"}, None)
        for result in cases:
            with self.subTest(result=result):
                if result == {**valid, 'extra': True}:
                    self.assertEqual(self.decode(fixture_session_fixture.answer_wire(self.token, result)).answer,
                                     '{"kind":"answer","message":"Visible.","proposal":null}')
                    continue
                failure = self.assert_malformed(json.dumps(fixture_session_fixture.answer_wire(self.token, result)), allowed=("answer",))
                if type(result) is dict and result.get("message") == "\ud800":
                    self.assertEqual(failure_issues(failure, path="$.action"),
                                     ("$.action.result.message: expected strict UTF-8 text without unpaired surrogates",))
