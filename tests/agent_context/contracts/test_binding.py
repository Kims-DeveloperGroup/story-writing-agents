from __future__ import annotations
from story_writing_agents.agent_context.invocation import context_execution as owner_context_execution
from story_writing_agents.agent_context.invocation import invocation_session as owner_invocation_session
from tests.agent_context.invocation import session_fixture as fixture_session_fixture
import unittest
import story_writing_agents
from dataclasses import FrozenInstanceError, fields, replace
from story_writing_agents import agent_context as agent_context_package
from story_writing_agents.agent_context.accounting import ProviderCallAccounting, ProviderCallKind, ProviderCallState, account_provider_payload
from story_writing_agents.agent_context.actions import ContextActionError
from unittest import mock
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers
from story_writing_agents.agent_context.contracts import errors as ctx_errors
from story_writing_agents.agent_context.contracts import events as ctx_events
from story_writing_agents.agent_context.contracts import session as ctx_session
from story_writing_agents.agent_context.invocation import delivery as ctx_delivery
from story_writing_agents.agent_context.invocation import provider as ctx_provider
from story_writing_agents.agent_context.invocation import requests as ctx_requests
from story_writing_agents.agent_context import manifest as ctx_manifest


class ServiceSurfaceAndBindingTests(unittest.TestCase):
    """Verify the direct surface, immutable binding, and construction boundary."""

    def test_exact_surface_states_fields_and_no_content_in_repr(self) -> None:
        owner_exports = (
            ('AgentContextService', owner_context_execution),
            ('ContextExecutionError', ctx_errors),
            ('ContextExecutionErrorCode', ctx_errors),
            ('ContextExecutionEvent', ctx_events),
            ('ContextExecutionEventKind', ctx_events),
            ('ContextNativeDisposition', ctx_events),
            ('ContextExecutionEventSink', ctx_events),
            ('ContextInvocationBinding', ctx_session),
            ('ContextInvocationKind', ctx_call_offers),
            ('ContextInvocationRequest', ctx_requests),
            ('ContextInvocationResult', ctx_requests),
            ('ContextInvocationSession', owner_invocation_session),
            ('ContextInvocationState', ctx_call_offers),
            ('ContextJsonObjectResultContract', ctx_call_offers),
            ('ContextProvider', ctx_provider),
            ('ContextProviderCallFinalizer', ctx_provider),
            ('ContextProviderCallRequest', ctx_provider),
            ('ContextProviderCallResult', ctx_provider),
            ('ContextProviderFailureFacts', ctx_provider),
            ('ContextContinuationRejection', ctx_provider),
            ('ContextProviderTerminalCode', ctx_provider),
            ('ContextReferencePreludeRequest', ctx_provider),
            ('ContextReferencePreludeResult', ctx_provider),
            ('ContextReadReceipt', ctx_session),
            ('ContextScope', ctx_manifest),
            ('ContextSourceClassification', ctx_delivery),
            ('context_provider_failure_facts', ctx_provider),
            ('record_context_provider_failure', ctx_provider),
        )
        self.assertEqual(
            tuple(name for name, owner in owner_exports if name in owner.__all__ and hasattr(owner, name)),
            tuple(name for name, _owner in owner_exports),
        )
        self.assertEqual((tuple(owner_invocation_session.__all__), tuple(owner_context_execution.__all__)), (('ContextInvocationSession',), ('AgentContextService',)))
        self.assertEqual( tuple(field.name for field in fields(ctx_events.ContextExecutionEvent)), ( "kind",
            "scope", "operation_id", "phase", "round_number", "action_index", "selected_source_count",
            "selected_source_utf8_bytes", "omitted_searchable_source_count",
            "omitted_searchable_source_utf8_bytes", "search_hit_count", "retrieved_source_count",
            "retrieved_source_utf8_bytes", "mandatory_reference_count", "mandatory_reference_utf8_bytes",
            "provider_input_tokens", "history_record_count", "observation_utf8_bytes", "truncated",
                'native_context_check', 'native_disposition', 'group_phase', 'attempt_id', 'invocation_id'), )
        progress = ctx_events.ContextExecutionEvent( ctx_events.ContextExecutionEventKind.BOOTSTRAP, fixture_session_fixture.SCOPE,
            "operation", selected_source_count=2, selected_source_utf8_bytes=128, omitted_searchable_source_count=1,
            omitted_searchable_source_utf8_bytes=64, mandatory_reference_count=0, mandatory_reference_utf8_bytes=0, )
        self.assertFalse(hasattr(progress, "__dict__"))
        for private in (fixture_session_fixture.PRIVATE_ANSWER, fixture_session_fixture.PRIVATE_BODY_A, fixture_session_fixture.PRIVATE_BODY_B): self.assertNotIn(private, repr(progress))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            progress.operation_id = "changed"  # type: ignore[misc]
        self.assertEqual(tuple(item.value for item in ctx_call_offers.ContextInvocationState),
            ("open", "searching", "reading", "answer_authorized", "failed"), )
        self.assertEqual(tuple(item.value for item in ctx_call_offers.ContextInvocationKind), ("lead",
                "specialist_assignment", "lead_synthesis", "specialist_advisory", ), )
        result_contract = ctx_call_offers.ContextJsonObjectResultContract("outcome-v1",
            fixture_session_fixture.closed_result_schema({"message": {"type": "string"}}), )
        self.assertEqual(result_contract.contract_id, "outcome-v1")
        self.assertFalse(hasattr(result_contract, "__dict__"))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            result_contract.contract_id = "changed"  # type: ignore[misc]
        self.assertEqual(tuple(item.value for item in ctx_provider.ContextProviderTerminalCode),
            ("malformed_structured_response", "response_correlation_mismatch", "response_semantic_rejected",
                "action_exhausted", "answer_validation_failed",
                "reference_runtime_failed", "context_runtime_failed", ), )
        for facade in (story_writing_agents, agent_context_package):
            self.assertFalse(hasattr(facade, "ContextProviderCallFinalizer"))
            self.assertFalse(hasattr(facade, "ContextProviderTerminalCode"))
            self.assertFalse(hasattr(facade, "ContextInvocationKind"))
            self.assertFalse(hasattr(facade, "ContextJsonObjectResultContract"))
        self.assertEqual( tuple(field.name for field in fields(ctx_provider.ContextProviderCallResult) ),
            ("response_text", "thread_id", "accounting", "finalizer", "context_witness",
                'native_context_check'), )
        witness_field = next(field for field in fields(ctx_provider.ContextProviderCallResult) if field.name == "context_witness")
        self.assertEqual((witness_field.default, witness_field.kw_only, witness_field.repr), (None, True, False))
        self.assertEqual(tuple(field.name for field in fields(ctx_session.ContextInvocationBinding)), ("scope",
                "operation_id", "result_correlation_id", "manifest", "reference_operation_id",
                "mandatory_references", ), )
        manifest, snapshots = fixture_session_fixture.manifest_fixture(); binding = fixture_session_fixture.binding(manifest)
        self.assertFalse(hasattr(binding, "__dict__")); self.assertNotIn(fixture_session_fixture.PRIVATE_BODY_A, repr(binding))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            binding.operation_id = "changed"  # type: ignore[misc]
        session = fixture_session_fixture.session(binding, fixture_session_fixture.registry(fixture_session_fixture.SCOPE, snapshots))
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.OPEN); self.assertEqual(session.next_action_index, 1)

    def test_binding_rejects_scope_identity_reference_order_and_unpaired_facts(self, ) -> None:
        manifest, _ = fixture_session_fixture.manifest_fixture(); references = (fixture_session_fixture.reference(1), fixture_session_fixture.reference(2, digest_character="5"))
        valid = fixture_session_fixture.binding(manifest, reference_operation_id="reference-operation", references=references, )
        self.assertEqual(valid.mandatory_references, references); other_scope = replace(fixture_session_fixture.SCOPE, session_id="other")
        cases = (lambda: ctx_session.ContextInvocationBinding( other_scope, "operation", "result", manifest ),
            lambda: ctx_session.ContextInvocationBinding(fixture_session_fixture.SCOPE, "", "result", manifest),
            lambda: fixture_session_fixture.binding(manifest, reference_operation_id="reference-operation"),
            lambda: fixture_session_fixture.binding(manifest, references=references), lambda: fixture_session_fixture.binding(
                manifest, reference_operation_id="reference-operation", references=references[::-1], ), )
        for case in cases:
            with self.assertRaises(ContextActionError): case()

    def test_response_rejection_facts_require_one_of_five_codes_and_submitted_calls(self, ) -> None:
        encoded, payload = account_provider_payload({"type": "object"},
            {"request": "content-free"}, call_kind=ProviderCallKind.COLLABORATION_INITIAL, )
        submitted = ProviderCallAccounting(payload,
            state=ProviderCallState.SUBMITTED, actual_transport_utf8_bytes=len(encoded), )
        prepared = ProviderCallAccounting(payload)
        for rejection in ctx_provider.ContextContinuationRejection:
            facts = ctx_provider.ContextProviderFailureFacts( submitted, continuation_rejection=rejection, )
            self.assertIs(facts.continuation_rejection, rejection)
            for incompatible in ({"timed_out": True}, {"submission_uncertain": True}):
                with self.subTest(rejection=rejection, incompatible=incompatible):
                    with self.assertRaises(ctx_errors.ContextExecutionError):
                        ctx_provider.ContextProviderFailureFacts(
                            submitted, continuation_rejection=rejection, **incompatible, )
        with self.assertRaises(ctx_errors.ContextExecutionError):
            ctx_provider.ContextProviderFailureFacts( submitted, continuation_rejection="continuation_unavailable", )
        rejection_codes = (ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
            ctx_provider.ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
            ctx_provider.ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
            ctx_provider.ContextProviderTerminalCode.ACTION_EXHAUSTED,
            ctx_provider.ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED, )
        self.assertEqual( tuple(field.name for field in fields(ctx_provider.ContextProviderFailureFacts) ),
            ( "accounting", "timed_out", "taint_existing_thread", "submitted_accountings",
            "submission_uncertain", "response_rejection_code", "continuation_rejection", "group_state",
                'native_context_check'), )
        for code in rejection_codes:
            with self.subTest(code=code):
                facts = ctx_provider.ContextProviderFailureFacts(
                    submitted_accountings=(submitted,), response_rejection_code=code, )
                failure = RuntimeError("private provider response")
                ctx_provider.record_context_provider_failure(failure, facts)
                self.assertIs(ctx_provider.context_provider_failure_facts( failure).response_rejection_code, code, )

        invalid = (lambda: ctx_provider.ContextProviderFailureFacts( submitted_accountings=(submitted,),
                response_rejection_code=( ctx_provider.ContextProviderTerminalCode.REFERENCE_RUNTIME_FAILED ), ),
            lambda: ctx_provider.ContextProviderFailureFacts( submitted_accountings=(submitted,),
                response_rejection_code=( ctx_provider.ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED ), ),
            lambda: ctx_provider.ContextProviderFailureFacts(response_rejection_code=(
                    ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE ), ),
            lambda: ctx_provider.ContextProviderFailureFacts(submitted_accountings=(prepared,),
                response_rejection_code=( ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE ), ),
            lambda: ctx_provider.ContextProviderFailureFacts( submitted_accountings=(submitted,),
                response_rejection_code="malformed_structured_response",  # type: ignore[arg-type]
            ), )
        for case in invalid:
            with self.subTest(case=case):
                with self.assertRaises(ctx_errors.ContextExecutionError) as caught: case()
                self.assertIs(caught.exception.code, ctx_errors.ContextExecutionErrorCode.INVALID_VALUE, )

    def test_entropy_failure_and_registry_scope_mismatch_are_content_free(self) -> None:
        manifest, snapshots = fixture_session_fixture.manifest_fixture(); binding = fixture_session_fixture.binding(manifest)
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, snapshots); registry.scope = replace(fixture_session_fixture.SCOPE, session_id="other")
        with self.assertRaises(ContextActionError): fixture_session_fixture.session(binding, registry)
        registry.scope = fixture_session_fixture.SCOPE
        for entropy in (b"short", None):
            with self.subTest(entropy=entropy):
                with mock.patch('story_writing_agents.agent_context.invocation.invocation_session.secrets.token_bytes',
                    return_value=entropy,):
                    with self.assertRaises(ContextActionError) as caught: owner_invocation_session.ContextInvocationSession(binding, registry)
                self.assertNotIn("PRIVATE", repr(caught.exception))
