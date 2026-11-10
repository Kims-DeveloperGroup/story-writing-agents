'Existing ContextExecutionFailureTests scenarios, moved without new cases.'
from __future__ import annotations
import asyncio
import json
from story_writing_agents.agent_context.group_observation import LeadGroupObservationKind
from tests.provider_wire_fixture import selection_plan
from contextlib import asynccontextmanager
from story_writing_agents.agent_context.manifest import ContextPriority, ContextSemanticChannel
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers, errors as ctx_errors, events as ctx_events
from story_writing_agents.agent_context.invocation import delivery as ctx_delivery, provider as ctx_provider
from story_writing_agents.agent_context.invocation import context_execution as owner_context_execution
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.invocation import execution_fixture as fixture_execution_fixture


class ContextExecutionFailureTests(fixture_execution_fixture.ContextExecutionTestCase):
    async def test_unmatched_strict_classification_fails_before_provider(self) -> None:
        request = self.request(source_classifications=(ctx_delivery.ContextSourceClassification(
            'source-not-in-authoritative-inventory', ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P2_WORKING, 0),))
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,))

        with self.assertRaises(ctx_errors.ContextExecutionError) as caught: await self.invoke(provider, request)

        self.assertIs(caught.exception.code, ctx_errors.ContextExecutionErrorCode.INVALID_VALUE)
        self.assertEqual(provider.calls, [])

    async def test_final_provider_call_is_answer_only_and_rejects_nonterminal(self) -> None:
        self.publish()
        request = self.request(session_id='session-exhausted', operation_id='operation-exhausted',
            result_correlation_id='result-exhausted')
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_search, fixture_execution_fixture.make_answer))

        with self.assertRaises(ctx_errors.ContextExecutionError) as caught: await self.invoke(provider, request)

        self.assertIs(caught.exception.code, ctx_errors.ContextExecutionErrorCode.ACTION_EXHAUSTED)
        self.assertEqual(len(provider.calls), 5)
        self.assertEqual([finalizer.calls for finalizer in provider.finalizers], [[('accepted',
            None)], [('accepted', None)], [('accepted', None)], [('accepted', None)], [('rejected',
            ctx_provider.ContextProviderTerminalCode.ACTION_EXHAUSTED)]])
        self.assertEqual(len(provider.responses), 1)
        self.assertEqual(
            tuple(call.payload["response_contract"]["allowed_action_types"] for call in provider.calls[:-1]),
            (["search_context", "submit_result"], ["search_context", "read_context", "submit_result"], ["search_context", "submit_result"], ["read_context", "submit_result"]),
        )
        self.assertEqual(provider.calls[-1].payload["response_contract"]["allowed_action_types"], ["submit_result"])

        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.TAINTED)
        self.assertIs(inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.INCOMPLETE)
        self.assertEqual(len(inspection.record.attempt.provider_calls), 5)
        self.assertIsNone(inspection.record.ready_checkpoint)
        self.assertEqual(len(provider.tainted), 1)
        self.assertEqual(provider.threads, {})

        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        before_request = fixture_execution_fixture.make_group_request(self.request(
            session_id="group-prelimit", operation_id="group-prelimit", result_correlation_id="group-prelimit-result",
            invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
            agent_id=LEAD_ORCHESTRATOR, physical_call_limit=1))
        before_provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_group_control("answer.workflow"), fixture_execution_fixture.make_group_answer))
        with self.assertRaises(ctx_errors.ContextExecutionError) as before_error:
            await self.invoke(before_provider, before_request)
        self.assertIs(before_error.exception.code, ctx_errors.ContextExecutionErrorCode.ACTION_EXHAUSTED)
        before_state = ctx_provider.context_provider_failure_facts(before_error.exception).group_state
        self.assertIs(before_state.workflow.phase, LeadGroupPhase.STOP)
        self.assertEqual(before_state.workflow.history, ())
        self.assertEqual(before_provider.calls, [])
        for label, limit, responses, plan, physical_calls, observed_index, search_allowed in (
                ("select-context", 3, (fixture_execution_fixture.make_search, fixture_execution_fixture.make_group_answer), selection_plan("answer.workflow", before_script_response=1), 3, 0, True),
                ("select-no-context", 2, (fixture_execution_fixture.make_group_answer,), selection_plan("answer.workflow"), 2, 0, False),
                ("generate-context", 3, (fixture_execution_fixture.make_search, fixture_execution_fixture.make_group_answer), selection_plan("answer.workflow"), 3, 1, True),
                ("generate-no-context", 2, (fixture_execution_fixture.make_group_answer,), selection_plan("answer.workflow"), 2, 1, False)):
            bounded_request = fixture_execution_fixture.make_group_request(self.request(
                operation_id=label, result_correlation_id=label + "-result",
                invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
                agent_id=LEAD_ORCHESTRATOR, physical_call_limit=limit))
            bounded_provider = fixture_execution_fixture.ScriptedProvider(responses, control_plan=plan)
            bounded = await self.invoke(bounded_provider, bounded_request)
            offered = {member.rule_graph.literal_at(("action_type",))
                       for member in bounded_provider.calls[observed_index].contract.action_index.contracts}
            self.assertEqual("search_context" in offered, search_allowed)
            self.assertEqual(len(bounded_provider.calls), limit)
            self.assertEqual(len(bounded.provider_calls), limit)
            final_offered = {member.rule_graph.literal_at(("action_type",))
                             for member in bounded_provider.calls[-1].contract.action_index.contracts}
            self.assertNotIn("read_context", final_offered)
        final_request = fixture_execution_fixture.make_group_request(self.request(
            session_id="group-stop", operation_id="group-stop", result_correlation_id="group-stop-result",
            invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
            agent_id=LEAD_ORCHESTRATOR, physical_call_limit=2))
        final_provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_group_answer,), control_plan=(
            (0, LeadGroupPhase.SELECT, fixture_execution_fixture.make_group_control("question.workflow")),
            (0, LeadGroupPhase.GENERATE, fixture_execution_fixture.make_group_control("answer.workflow"))))
        final_events = []
        with self.assertRaises(ctx_errors.ContextExecutionError) as final_error:
            await self.invoke(final_provider, final_request, event_sink=final_events.append)
        self.assertIs(final_error.exception.code, ctx_errors.ContextExecutionErrorCode.ACTION_EXHAUSTED)
        stopped = ctx_provider.context_provider_failure_facts(final_error.exception).group_state.workflow
        self.assertIs(stopped.phase, LeadGroupPhase.STOP)
        self.assertEqual(stopped.stop_reason, "provider_call_budget_exhausted")
        self.assertEqual(tuple(item.group_id for item in stopped.history), ("question.workflow", "answer.workflow"))
        self.assertEqual(len(final_provider.calls), 2)
        self.assertEqual([item.calls for item in final_provider.finalizers], [[("accepted", None)]] * 2)
        self.assertEqual(final_provider.responses, [fixture_execution_fixture.make_group_answer])
        self.assertFalse(any(event.kind in (ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED,
                                           ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED) for event in final_events))
        final_inspection = self.inspect(final_provider, final_request)
        self.assertIs(final_inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.INCOMPLETE)
        self.assertIsNone(final_inspection.record.ready_checkpoint)

    async def test_prepared_submitted_timeout_cancel_and_malformed_outcomes_are_exact(self) -> None:
        cases: tuple[tuple[str, BaseException | str, owner_record_values.ContextLedgerAttemptOutcome, bool], ...] = ((
            'prepared', RuntimeError('PRIVATE_PREPARED'), owner_record_values.ContextLedgerAttemptOutcome.FAILED,
            False), ('malformed', '{', owner_record_values.ContextLedgerAttemptOutcome.MALFORMED, True))
        for label, response, expected, tainted in cases:
            with self.subTest(label=label):
                request = self.request( session_id=f"session-{label}", operation_id=f"operation-{label}",
                    result_correlation_id=f"result-{label}",)
                provider = fixture_execution_fixture.ScriptedProvider((response,))
                with self.assertRaises(BaseException): await self.invoke(provider, request)
                inspection = self.inspect(provider, request)
                self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.TAINTED)
                self.assertIs(inspection.record.attempt.outcome, expected)
                self.assertEqual(bool(provider.tainted), tainted)
                if label == "malformed": self.assertEqual(provider.finalizers[0].calls, [('rejected',
                        ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)])

        for (label, failure, expected) in ((('timeout', RuntimeError('PRIVATE_TIMEOUT'),
            owner_record_values.ContextLedgerAttemptOutcome.TIMED_OUT), ('cancel',
            asyncio.CancelledError('PRIVATE_CANCEL'), owner_record_values.ContextLedgerAttemptOutcome.CANCELLED))):
            request = self.request(session_id=f'session-{label}', operation_id=f'operation-{label}',
                result_correlation_id=f'result-{label}')
            provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,))

            def fail_submitted(call: ctx_provider.ContextProviderCallRequest, active: fixture_execution_fixture.ScriptedProvider):
                accounting = active._accounting(call)
                ctx_provider.record_context_provider_failure(failure, ctx_provider.ContextProviderFailureFacts(accounting,
                    timed_out=label == 'timeout', taint_existing_thread=True))
                return failure

            provider.responses[:] = [fail_submitted]
            with self.assertRaises(type(failure)): await self.invoke(provider, request)
            inspection = self.inspect(provider, request)
            self.assertIs(inspection.record.attempt.outcome, expected)
            self.assertTrue(provider.tainted)

        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        for uncertain in (False, True):
            label = "group-unknown" if uncertain else "group-submitted-timeout"
            grouped_request = fixture_execution_fixture.make_group_request(self.request(
                session_id=label, operation_id=label, result_correlation_id=label + "-result",
                invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
                agent_id=LEAD_ORCHESTRATOR, physical_call_limit=3))
            failure = RuntimeError(label)
            def fail_group_generation(call, active):
                accounting = None if uncertain else active._accounting(call)
                ctx_provider.record_context_provider_failure(failure, ctx_provider.ContextProviderFailureFacts(
                    accounting, timed_out=not uncertain, taint_existing_thread=True,
                    submission_uncertain=uncertain))
                return failure
            grouped_provider = fixture_execution_fixture.ScriptedProvider((fail_group_generation, fixture_execution_fixture.make_group_answer), control_plan=selection_plan("answer.workflow"))
            grouped_events = []
            failure_observations = []
            with self.assertRaises(RuntimeError) as caught:
                await self.invoke(grouped_provider, grouped_request, event_sink=grouped_events.append, group_observation_sink=failure_observations.append)
            self.assertIs(caught.exception, failure)
            facts = ctx_provider.context_provider_failure_facts(caught.exception)
            self.assertIs(facts.submission_uncertain, uncertain)
            self.assertIs(facts.timed_out, not uncertain)
            self.assertIsNot(facts.group_state.workflow.phase, LeadGroupPhase.DONE)
            self.assertEqual(len(facts.group_state.workflow.history), 1)
            self.assertEqual(len(grouped_provider.calls), 2)
            self.assertEqual(grouped_provider.responses, [fixture_execution_fixture.make_group_answer])
            self.assertFalse(any(event.kind is ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED for event in grouped_events))
            inspection = self.inspect(grouped_provider, grouped_request)
            self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.TAINTED)
            self.assertIsNone(inspection.record.ready_checkpoint)

            observed_bounds = {item.call_id: item for item in failure_observations
                               if item.kind is LeadGroupObservationKind.BOUND}
            self.assertEqual(set(observed_bounds), {call.call_id for call in grouped_provider.calls})
            self.assertEqual(tuple(observed_bounds[call.call_id].phase for call in grouped_provider.calls),
                             (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE))
            self.assertIs(failure_observations[-1].kind, LeadGroupObservationKind.TERMINAL)
            self.assertNotEqual(failure_observations[-1].terminal_code, "accepted")
            self.assertEqual(tuple(json.loads(value) for value in failure_observations[-1].selection_snapshots),
                             tuple(item.as_object() for item in facts.group_state.workflow.history))
            from story_writing_agents.agent_context.group_observation import LeadGroupTrace
            from story_writing_agents.agent_context.group_reporting import group_trace_report
            report = group_trace_report(LeadGroupTrace(label, tuple(failure_observations),
                failure_observations[-1].terminal_code),
                tuple((item.call_id, item.accounting)
                      for item in inspection.record.attempt.provider_calls),
                unmeasured_prepared_call_ids=(grouped_provider.calls[-1].call_id,) if uncertain else ())
            self.assertEqual(report["known_submitted_calls"], 1 if uncertain else 2)
            self.assertEqual(report["prepared_only_calls"], 0)
            self.assertEqual(report["unresolved_calls"], int(uncertain))
            self.assertIs(report["submission_coverage"]["complete"], not uncertain)
            self.assertEqual(report["submission_coverage"]["unresolved_call_ids"],
                [grouped_provider.calls[-1].call_id] if uncertain else [])
            self.assertEqual(report["known_submitted_subtotals"]["calls"], 1 if uncertain else 2)
            if uncertain:
                self.assertIsNone(report["submitted_calls"])
                self.assertIsNone(report["transport_utf8_bytes"])
                self.assertIsNone(report["rates"]["repair_calls_per_submitted_call"]["denominator"])
            else:
                self.assertEqual(report["submitted_calls"], 2)
            self.assertFalse(report["result_accepted"])

    async def test_invalid_read_response_rejects_taints_and_never_advances(self) -> None:
        private_values = ("conflict", "PRIVATE/critique text")

        def invalid_read(call: ctx_provider.ContextProviderCallRequest, _provider: fixture_execution_fixture.ScriptedProvider) -> str:
            return json.dumps({'agent_action': {'action_type': 'read_context', 'read_handles': list(
                private_values)}}, ensure_ascii=False, separators=(',', ':'))

        self.publish()
        request = self.request(session_id="session-invalid-read")
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, invalid_read))
        events: list[ctx_events.ContextExecutionEvent] = []

        with self.assertRaises(ctx_errors.ContextExecutionError) as caught:
            await self.invoke(provider, request, event_sink=events.append)

        failure = caught.exception
        self.assertIs(failure.code, ctx_errors.ContextExecutionErrorCode.MALFORMED_ACTION)
        self.assertEqual( str(failure), "contextual provider read action is invalid: `handles` must contain "
            "source handles returned by the preceding context search",)
        rendered = '\n'.join((str(failure), repr(failure), repr(failure.__cause__), repr(failure.__context__)))
        for private in private_values: self.assertNotIn(private, rendered)
        self.assertEqual(tuple((finalizer.calls for finalizer in provider.finalizers)), ([(
            'accepted', None)], [('rejected', ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)]))
        self.assertEqual(len(provider.tainted), 1)
        self.assertEqual(provider.threads, {})
        self.assertIn(ctx_events.ContextExecutionEventKind.TAINTED, {event.kind for event in events})
        self.assertIs(events[-1].kind, ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.TAINTED)
        self.assertIs(inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.MALFORMED)
        self.assertIsNone(inspection.record.ready_checkpoint)

    async def test_removed_correlation_field_is_rejected_without_disclosure(self) -> None:
        private_correlation = "PRIVATE critique prose\x1b]unsafe\x07"

        def mismatch(_call: ctx_provider.ContextProviderCallRequest, _provider: fixture_execution_fixture.ScriptedProvider) -> str:
            return json.dumps({'correlation': private_correlation, 'agent_action': {
                'action_type': 'read_context', 'read_handles': []}}, ensure_ascii=False, separators=(',', ':'))

        self.publish()
        cases = (('normal', (mismatch,)), ('max-action', (fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_search,
            fixture_execution_fixture.make_read_search_hit, mismatch)))
        expected = "contextual provider action is malformed"
        for label, responses in cases:
            with self.subTest(label=label):
                request = self.request(session_id=f'session-correlation-{label}',
                    operation_id=f'operation-correlation-{label}', result_correlation_id=f'result-correlation-{label}')
                provider = fixture_execution_fixture.ScriptedProvider(responses)
                events: list[ctx_events.ContextExecutionEvent] = []

                with self.assertRaises(ctx_errors.ContextExecutionError) as caught:
                    await self.invoke(provider, request, event_sink=events.append)

                failure = caught.exception
                self.assertIs(failure.code, ctx_errors.ContextExecutionErrorCode.MALFORMED_ACTION)
                self.assertEqual(str(failure), expected)
                expected_token = fixture_execution_fixture.make_correlation(provider.calls[-1])
                rendered = '\n'.join((str(failure), repr(failure), repr(failure.__cause__), repr(failure.__context__)))
                for private in ((expected_token, private_correlation, 'PRIVATE critique prose', '\x1b', '\x07')):
                    self.assertNotIn(private, rendered)
                self.assertEqual([finalizer.calls for finalizer in provider.finalizers[:-1]], [[(
                    'accepted', None)]] * (len(provider.finalizers) - 1))
                self.assertEqual(provider.finalizers[-1].calls, [('rejected',
                    ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)])
                self.assertEqual(len(provider.tainted), 1)
                self.assertEqual(provider.threads, {})
                self.assertIs(events[-1].kind, ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
                inspection = self.inspect(provider, request)
                self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.TAINTED)
                self.assertIs(inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.MALFORMED)
                self.assertIsNone(inspection.record.ready_checkpoint)

    async def test_output_and_source_failures_never_advance_ready(self) -> None:
        forged = "ctxh_v1_" + "0" * 64

        def invalid_output(call: ctx_provider.ContextProviderCallRequest, provider: fixture_execution_fixture.ScriptedProvider) -> str:
            del call, provider
            return json.dumps({'agent_action': {'action_type': 'submit_result', 'task_result': {
                'content': fixture_execution_fixture.PRIVATE_ANSWER}, 'used_handles': [forged]}}, separators=(',', ':'))

        request = self.request(session_id="session-output")
        provider = fixture_execution_fixture.ScriptedProvider((invalid_output,))
        with self.assertRaises(Exception): await self.invoke(provider, request)
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.MALFORMED)
        self.assertIsNone(inspection.record.ready_checkpoint)
        self.assertEqual(provider.finalizers[0].calls, [('rejected',
            ctx_provider.ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)])

        self.publish()
        source_request = self.request(session_id='session-source', operation_id='operation-source',
            result_correlation_id='result-source')

        def drift(call: ctx_provider.ContextProviderCallRequest, active: fixture_execution_fixture.ScriptedProvider) -> str:
            path = self.workspace.root / "manuscript/part-01/chapter-01.md"
            path.write_text("changed after provider success\n", encoding="utf-8")
            return fixture_execution_fixture.make_answer(call, active)

        source_provider = fixture_execution_fixture.ScriptedProvider((drift,))
        with self.assertRaises(Exception): await self.invoke(source_provider, source_request)
        inspection = self.inspect(source_provider, source_request)
        self.assertIs(inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.SOURCE_INVALID)
        self.assertIsNone(inspection.record.ready_checkpoint)
        self.assertEqual(source_provider.finalizers[0].calls, [('post_response_runtime_failure',
            ctx_provider.ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED)])

        class _ExitFailureProvider(fixture_execution_fixture.ScriptedProvider):
            @asynccontextmanager
            async def invocation(self, *args, **kwargs):
                async with super().invocation(*args, **kwargs): yield
                raise RuntimeError("session-root postvalidation failed")

        exit_request = self.request(session_id="session-exit")
        exit_provider = _ExitFailureProvider((fixture_execution_fixture.make_answer,))
        with self.assertRaisesRegex(RuntimeError, "session-root postvalidation failed"):
            await self.invoke(exit_provider, exit_request)
        self.assertIsNone(self.inspect(exit_provider, exit_request).record.ready_checkpoint)
        self.assertEqual(exit_provider.finalizers[0].calls, [('post_response_runtime_failure',
            ctx_provider.ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED)])

    def test_frozen_failure_mapping_covers_all_service_boundaries(self) -> None:
        cases = ((ctx_errors.ContextExecutionError(ctx_errors.ContextExecutionErrorCode.ACTION_EXHAUSTED), 'exhausted',
            owner_record_values.ContextLedgerAttemptOutcome.INCOMPLETE), (RuntimeError(), 'provider',
            owner_record_values.ContextLedgerAttemptOutcome.FAILED), (RuntimeError(), 'decode',
            owner_record_values.ContextLedgerAttemptOutcome.MALFORMED), (RuntimeError(), 'reference',
            owner_record_values.ContextLedgerAttemptOutcome.REFERENCE_INVALID), (RuntimeError(), 'reference_complete',
            owner_record_values.ContextLedgerAttemptOutcome.REFERENCE_INVALID), (RuntimeError(), 'source',
            owner_record_values.ContextLedgerAttemptOutcome.SOURCE_INVALID), (RuntimeError(), 'source_revalidation',
            owner_record_values.ContextLedgerAttemptOutcome.SOURCE_INVALID), (RuntimeError(), 'answer',
            owner_record_values.ContextLedgerAttemptOutcome.OUTPUT_INVALID))
        for failure, stage, expected in cases:
            with self.subTest(stage=stage):
                self.assertIs(owner_context_execution.AgentContextService._failure_outcome(failure, stage=stage,
                    history_advanced=False, terminal_provider_succeeded=False), expected)
        self.assertIs(owner_context_execution.AgentContextService._failure_outcome(RuntimeError(), stage='commit',
            history_advanced=False, terminal_provider_succeeded=True), owner_record_values.ContextLedgerAttemptOutcome.AMBIGUOUS)
        self.assertIs(owner_context_execution.AgentContextService._failure_outcome(RuntimeError(),
            stage='observation_delivery', history_advanced=True, terminal_provider_succeeded=False),
            owner_record_values.ContextLedgerAttemptOutcome.AMBIGUOUS)
