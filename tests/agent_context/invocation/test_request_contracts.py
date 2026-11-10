'Existing ContextExecutionValueTests scenarios, moved without new cases.'
from __future__ import annotations
from dataclasses import FrozenInstanceError, fields, replace
from types import SimpleNamespace
from unittest import mock
from story_writing_agents.agent_context.accounting import ProviderCallAccounting, ProviderCallKind, ProviderCallState, account_provider_payload
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextActionPolicy
from story_writing_agents.agent_context.manifest import ContextPriority, ContextSemanticChannel, ContextSubjectKind
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers, context_values as ctx_context_values, errors as ctx_errors, planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.invocation import delivery as ctx_delivery, provider as ctx_provider, requests as ctx_requests
from story_writing_agents.agent_context.invocation import context_execution as owner_context_execution
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from tests.agent_context.invocation import execution_fixture as fixture_execution_fixture


class ContextExecutionValueTests(fixture_execution_fixture.ContextExecutionTestCase):
    def test_workspace_identity_values_errors_and_reprs_are_content_free(self) -> None:
        expected = fixture_execution_fixture.digest_bytes(
            b'story-writing-agents-context-workspace-v1\x00' + self.workspace.root.as_posix().encode('utf-8'))
        self.assertEqual(self.service.workspace_id, expected)
        self.assertEqual(tuple((item.value for item in ctx_errors.ContextExecutionErrorCode)), (
            'invalid_value', 'provider_contract', 'malformed_action', 'action_exhausted', 'continuation_mismatch'))
        self.assertEqual(tuple((field.name for field in fields(ctx_requests.ContextInvocationRequest))), (
            'session_id', 'subject_kind', 'subject_id', 'agent_id', 'operation_id',
            'result_correlation_id', 'materials', 'result_contract', 'source_classifications',
            'reference_operation_id', 'mandatory_references', 'fresh',
            'allowed_search_source_kinds', 'invocation_kind', 'physical_call_limit',
            'include_core_statuses', 'session_state_json', 'execution_workspace', 'group_state'))
        request = self.request()
        self.assertFalse(hasattr(request, "__dict__"))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            request.session_id = "changed"  # type: ignore[misc]
        self.assertNotIn(fixture_execution_fixture.PRIVATE_INPUT, repr(request))
        for code in ctx_errors.ContextExecutionErrorCode:
            failure = ctx_errors.ContextExecutionError(code)
            self.assertNotIn("PRIVATE", repr(failure))
            self.assertIsNone(failure.__cause__)

    def test_lead_contract_pair_and_physical_call_limit_are_strict(self) -> None:
        contract = fixture_execution_fixture.LEAD_RESULT_CONTRACT
        request = self.request(invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=contract,
            physical_call_limit=3)
        self.assertIs(request.invocation_kind, ctx_call_offers.ContextInvocationKind.LEAD)
        self.assertEqual(request.result_contract, contract)
        self.assertEqual(request.physical_call_limit, 3)
        invalid = ({'subject_kind': ContextSubjectKind.SESSION, 'subject_id': 'session'}, {
            'result_contract': None}, {'result_contract': object()}, {'physical_call_limit': 0}, {
            'physical_call_limit': 6}, {'physical_call_limit': True})
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ctx_errors.ContextExecutionError) as caught: self.request(**overrides)
                self.assertIs( caught.exception.code, ctx_errors.ContextExecutionErrorCode.INVALID_VALUE, )

    def test_provider_failure_facts_are_exact_optional_and_chainless(self) -> None:
        failure = RuntimeError("PRIVATE_PROVIDER_EXCEPTION")
        encoded, payload = account_provider_payload({'type': 'object'}, {'value': 'bounded'},
            call_kind=ProviderCallKind.COLLABORATION_INITIAL)
        submitted = ProviderCallAccounting(payload, state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(encoded))
        facts = ctx_provider.ContextProviderFailureFacts(accounting=submitted, timed_out=True,
            taint_existing_thread=True, submitted_accountings=(submitted,), submission_uncertain=True)
        ctx_provider.record_context_provider_failure(failure, facts)
        self.assertIs(ctx_provider.context_provider_failure_facts(failure), facts)
        self.assertEqual(ctx_provider.context_provider_failure_facts(ValueError()), ctx_provider.ContextProviderFailureFacts( ))
        self.assertNotIn("PRIVATE_PROVIDER_EXCEPTION", repr(facts))
        prepared = replace(submitted, state=ProviderCallState.PREPARED, actual_transport_utf8_bytes=None)
        for changes in (({'submitted_accountings': (prepared,)},
            {'submitted_accountings': [submitted]}, {'submission_uncertain': 1})):
            with (self.subTest(changes=changes), self.assertRaises(ctx_errors.ContextExecutionError)):
                replace(facts, **changes)

    def test_conditional_source_classification_is_exact(self) -> None:
        required = ctx_delivery.ContextSourceClassification('source-required',
            ContextSemanticChannel.HARD_CONSTRAINT, ContextPriority.P2_WORKING, 0)
        self.assertIs(required.allow_absent, False)
        conditional = ctx_delivery.ContextSourceClassification('source-conditional',
            ContextSemanticChannel.HARD_CONSTRAINT, ContextPriority.P2_WORKING, 1, allow_absent=True)
        self.assertIs(conditional.allow_absent, True)
        for (channel, priority, allow_absent) in (((ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P2_WORKING, 1), (ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P3_OPTIONAL, True), (ContextSemanticChannel.OPTIONAL_BACKGROUND,
            ContextPriority.P2_WORKING, True))):
            with (self.subTest(channel=channel, priority=priority, allow_absent=allow_absent)):
                with self.assertRaises(ctx_errors.ContextExecutionError) as caught:
                    ctx_delivery.ContextSourceClassification("source-invalid", channel, priority, 0, allow_absent=allow_absent)
                self.assertIs( caught.exception.code, ctx_errors.ContextExecutionErrorCode.INVALID_VALUE, )

    def test_composite_policy_fingerprint_covers_wire_planning_and_every_limit(self) -> None:
        action_names = tuple(field.name for field in fields(ContextActionPolicy))
        source_names = tuple(field.name for field in fields(owner_capture_values.ContextSourcePolicy))
        self.assertEqual(action_names, owner_context_execution._ACTION_POLICY_FINGERPRINT_FIELDS)
        self.assertEqual(source_names, owner_context_execution._SOURCE_POLICY_FINGERPRINT_FIELDS)
        self.assertEqual((len(action_names), len(source_names)), (19, 7))
        fingerprint = owner_context_execution._execution_policy_fingerprint
        policy, actions, sources = ctx_planning_inputs.DEFAULT_CONTEXT_PLANNING_POLICY, CONTEXT_ACTION_POLICY, owner_capture_values.CONTEXT_SOURCE_POLICY
        baseline = fingerprint(policy, actions, sources)
        self.assertEqual(ctx_context_values.CONTEXT_WIRE_VERSION, "agent-provider-wire-v11")
        with mock.patch.object(owner_context_execution, 'CONTEXT_WIRE_VERSION', "agent-context-wire-v7"):
            self.assertEqual(fingerprint(policy, actions, sources),
                             "sha256:d8cfd92e6949dbb817dfb7253cff1865642493d46078fa1e042a64ef81740a18")
        variants = {fingerprint(replace(policy, max_delta_depth=15), actions, sources)}
        for fields_, original, is_action in ((action_names, actions, True), (source_names, sources, False)):
            values = {name: getattr(original, name) for name in fields_}
            for name in fields_:
                changed = SimpleNamespace(**{**values, name: values[name] + 1})
                variants.add(fingerprint(policy, changed if is_action else actions, sources if is_action else changed))
        with mock.patch.object(owner_context_execution, 'CONTEXT_WIRE_VERSION', "agent-context-wire-v4-test"):
            variants.add(fingerprint(policy, actions, sources))
        self.assertNotIn(baseline, variants)
        self.assertEqual(len(variants), 28)
