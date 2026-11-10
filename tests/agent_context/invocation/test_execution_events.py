'Existing ContextExecutionObserverTests scenarios, moved without new cases.'
from __future__ import annotations
import asyncio
import story_writing_agents
from collections.abc import Callable
from dataclasses import fields
from unittest import mock
from story_writing_agents import agent_context as agent_context_package
from story_writing_agents.agent_context.accounting import ProviderUsage
from story_writing_agents.agent_context.actions import ContextActionError
from story_writing_agents.agent_context.manifest import ContextSubjectKind
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.agent_context.contracts import errors as ctx_errors, events as ctx_events, planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.invocation import provider as ctx_provider, requests as ctx_requests
from story_writing_agents.agent_context.invocation import context_execution as owner_context_execution
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.invocation import execution_fixture as fixture_execution_fixture


class ContextExecutionObserverTests(fixture_execution_fixture.ContextExecutionTestCase):
    def test_direct_event_surface_and_strict_content_free_fact_contract(self) -> None:
        self.assertIn("ContextExecutionEvent", ctx_events.__all__)
        self.assertIn("ContextExecutionEventKind", ctx_events.__all__)
        self.assertIn("ContextExecutionEventSink", ctx_events.__all__)
        for facade in (story_writing_agents, agent_context_package):
            self.assertFalse(hasattr(facade, "ContextExecutionEvent"))
            self.assertFalse(hasattr(facade, "ContextExecutionEventKind"))
            self.assertFalse(hasattr(facade, "ContextExecutionEventSink"))
        self.assertEqual(tuple((item.value for item in ctx_events.ContextExecutionEventKind)), ('bootstrap',
            'delta', 'omission', 'search_started', 'search_completed', 'read_started',
            'read_completed', 'answer_authorized', 'invalidated', 'budget_exhausted',
            'action_exhausted', 'tainted', 'checkpoint_advanced', 'checkpoint_not_advanced',
                'native_context'))
        self.assertEqual(tuple((field.name for field in fields(ctx_events.ContextExecutionEvent))), ('kind',
            'scope', 'operation_id', 'phase', 'round_number', 'action_index',
            'selected_source_count', 'selected_source_utf8_bytes',
            'omitted_searchable_source_count', 'omitted_searchable_source_utf8_bytes',
            'search_hit_count', 'retrieved_source_count', 'retrieved_source_utf8_bytes',
            'mandatory_reference_count', 'mandatory_reference_utf8_bytes', 'provider_input_tokens',
            'history_record_count', 'observation_utf8_bytes', 'truncated',
                'native_context_check', 'native_disposition', 'group_phase', 'attempt_id', 'invocation_id'))
        scope = self.service.scope('session', ContextSubjectKind.CONVERSATION, 'conversation', LEAD_ORCHESTRATOR)
        plan_facts = {'selected_source_count': 0, 'selected_source_utf8_bytes': 0,
            'omitted_searchable_source_count': 0, 'omitted_searchable_source_utf8_bytes': 0,
            'mandatory_reference_count': 0, 'mandatory_reference_utf8_bytes': 0}
        event = ctx_events.ContextExecutionEvent(ctx_events.ContextExecutionEventKind.BOOTSTRAP, scope, 'operation', **plan_facts)
        self.assertTrue(repr(event).startswith("ContextExecutionEvent("))
        for private_name in (('prompt', 'query', 'candidate', 'excerpt', 'reference_body',
            'response', 'path', 'digest', 'capabilities', 'thread_id')):
            self.assertNotIn(private_name, {field.name for field in fields(event)})
        invalid_events = (lambda: ctx_events.ContextExecutionEvent(ctx_events.ContextExecutionEventKind.BOOTSTRAP, scope,
            '', **plan_facts), lambda: ctx_events.ContextExecutionEvent(ctx_events.ContextExecutionEventKind.BOOTSTRAP,
            scope, 'operation', action_index=1, **plan_facts), lambda: ctx_events.ContextExecutionEvent(
            ctx_events.ContextExecutionEventKind.OMISSION, scope, 'operation',
            omitted_searchable_source_count=0, omitted_searchable_source_utf8_bytes=0),
            lambda: ctx_events.ContextExecutionEvent(ctx_events.ContextExecutionEventKind.SEARCH_COMPLETED, scope,
            'operation', action_index=1, search_hit_count=0, observation_utf8_bytes=0),
            lambda: ctx_events.ContextExecutionEvent(ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED, scope,
            'operation', action_index=1))
        for build in invalid_events:
            with self.subTest(build=build):
                with self.assertRaises((ctx_errors.ContextExecutionError, ContextActionError)): build()

    async def test_bootstrap_delta_and_no_search_emit_exact_plan_and_usage_facts(self) -> None:
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer, fixture_execution_fixture.make_answer), usage=ProviderUsage(input_tokens=7, output_tokens=3))
        first_events: list[ctx_events.ContextExecutionEvent] = []
        first = await self.invoke(provider, event_sink=first_events.append)
        second_events: list[ctx_events.ContextExecutionEvent] = []
        second = await self.invoke(provider, self.request(operation_id='operation-2',
            result_correlation_id='result-2'), event_sink=second_events.append)
        for (events, planned_kind) in (((first_events, ctx_events.ContextExecutionEventKind.BOOTSTRAP),
            (second_events, ctx_events.ContextExecutionEventKind.DELTA))):
            self.assertIs(events[0].kind, planned_kind)
            self.assertEqual(tuple((event.kind for event in events[-2:])), (
                ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED, ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED))
            self.assertTrue(len(events) == 3 or (len(events) == 4 and events[
                1].kind is ctx_events.ContextExecutionEventKind.OMISSION))
        for (result, events, operation_id) in (((first, first_events, 'operation'), (second,
            second_events, 'operation-2'))):
            plan = events[0]
            accounting = result.projection_accounting
            self.assertEqual((plan.selected_source_count, plan.selected_source_utf8_bytes,
                plan.omitted_searchable_source_count, plan.omitted_searchable_source_utf8_bytes,
                plan.mandatory_reference_count, plan.mandatory_reference_utf8_bytes), (
                accounting.selected_logical_source_count, accounting.selected_logical_utf8_bytes,
                accounting.searchable.unprojected_source_count,
                accounting.searchable.unprojected_source_utf8_bytes,
                accounting.mandatory_reference_count, accounting.mandatory_reference_utf8_bytes))
            self.assertEqual({event.operation_id for event in events}, {operation_id})
            self.assertTrue(all(event.phase is None for event in events))
            self.assertTrue(all(event.round_number is None for event in events))
            self.assertEqual(events[-2].action_index, 1)
            self.assertEqual(events[-1].provider_input_tokens, 7)
            self.assertIsNone(events[-1].action_index)

    async def test_omission_search_read_and_answer_emit_paired_monotonic_facts(self) -> None:
        self.publish()
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_answer_with_read),
            usage=ProviderUsage(input_tokens=5))
        events: list[ctx_events.ContextExecutionEvent] = []
        result = await self.invoke(provider, event_sink=events.append)
        self.assertEqual(tuple((event.kind for event in events)), (
            ctx_events.ContextExecutionEventKind.BOOTSTRAP, ctx_events.ContextExecutionEventKind.OMISSION,
            ctx_events.ContextExecutionEventKind.SEARCH_STARTED, ctx_events.ContextExecutionEventKind.SEARCH_COMPLETED,
            ctx_events.ContextExecutionEventKind.READ_STARTED, ctx_events.ContextExecutionEventKind.READ_COMPLETED,
            ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED, ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED))
        plan, omission, search_start, search_done, read_start, read_done, answer, terminal = events
        self.assertEqual((omission.omitted_searchable_source_count,
            omission.omitted_searchable_source_utf8_bytes), (plan.omitted_searchable_source_count,
            plan.omitted_searchable_source_utf8_bytes))
        self.assertGreater(omission.omitted_searchable_source_count or 0, 0)
        self.assertEqual((search_start.action_index, search_done.action_index), (1, 1))
        self.assertGreater(search_done.search_hit_count or 0, 0)
        self.assertGreater(search_done.observation_utf8_bytes or 0, 0)
        self.assertIs(search_done.truncated, False)
        self.assertEqual((read_start.action_index, read_done.action_index), (2, 2))
        self.assertEqual(read_done.retrieved_source_count, 1)
        self.assertEqual(read_done.retrieved_source_utf8_bytes, len(fixture_execution_fixture.PRIVATE_ARTIFACT.encode('utf-8')))
        self.assertEqual(read_done.history_record_count, 0)
        self.assertGreater(read_done.observation_utf8_bytes or 0, 0)
        self.assertEqual(answer.action_index, 3)
        self.assertEqual(terminal.provider_input_tokens, 15)
        self.assertEqual( result.authorization.used_evidence, result.authorization.completed_evidence )
        terminal_indexes = [index for index, event in enumerate(events) if event.kind in {
            ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED, ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED}]
        self.assertEqual(terminal_indexes, [len(events) - 1])

    async def test_local_reference_load_does_not_change_provider_usage(self) -> None:
        for (label, answer_usage, expected) in ((('exact', ProviderUsage(input_tokens=13), 13),
            ('missing-answer', None, None))):
            with self.subTest(label=label):
                reference = self.reference(f'reference-{label}')
                request = self.request( session_id=f"session-{label}", operation_id=f"operation-{label}",
                    result_correlation_id=f"result-{label}",
                    reference_operation_id=f"reference-{label}", mandatory_references=(reference,), )
                provider = fixture_execution_fixture.ScriptedProvider( (fixture_execution_fixture.make_answer,),
                    reference_operation_id=f"reference-{label}", usage=answer_usage, )
                events: list[ctx_events.ContextExecutionEvent] = []
                await self.invoke(provider, request, event_sink=events.append)
                self.assertEqual(events[0].mandatory_reference_count, 1)
                self.assertEqual(events[0].mandatory_reference_utf8_bytes, len(b'PRIVATE_REFERENCE_BODY'))
                self.assertEqual(events[-1].provider_input_tokens, expected)

    async def test_debate_observation_requires_and_preserves_phase_and_round(self) -> None:
        request = self.request(subject_kind=ContextSubjectKind.DEBATE, subject_id='debate',
            operation_id='debate_propose_revision_operation')
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer, fixture_execution_fixture.make_answer))
        with self.assertRaises(ctx_errors.ContextExecutionError):
            await self.invoke(provider, request, event_sink=lambda _event: None)
        self.assertEqual(provider.calls, [])
        events: list[ctx_events.ContextExecutionEvent] = []
        await self.invoke(provider, request, event_sink=events.append, phase='propose_revision', round_number=0)
        self.assertTrue(events)
        self.assertEqual({event.phase for event in events}, {"propose_revision"})
        self.assertEqual({event.round_number for event in events}, {0})

    async def test_observer_forms_are_rejected_before_provider_or_ledger_effects(self) -> None:
        async def declared_async(_event: ctx_events.ContextExecutionEvent) -> None: return None

        async def declared_async_generator(_event: ctx_events.ContextExecutionEvent): yield None

        class AsyncCallable:
            async def __call__(self, _event: ctx_events.ContextExecutionEvent) -> None: return None

        def dynamic_awaitable(_event: ctx_events.ContextExecutionEvent) -> object:
            async def result() -> None: return None

            return result()

        def dynamic_async_generator(_event: ctx_events.ContextExecutionEvent) -> object:
            async def result(): yield None

            return result()

        for (label, sink) in ((('noncallable', object()), ('declared-async', declared_async),
            ('declared-async-generator', declared_async_generator), ('async-callable', AsyncCallable()))):
            with self.subTest(label=label):
                provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,))
                with self.assertRaises(ctx_errors.ContextExecutionError):
                    await self.invoke(provider, event_sink=sink)  # type: ignore[arg-type]
                self.assertEqual(provider.calls, [])
                self.assertEqual(provider.inspections, [])
        for (label, sink) in ((('dynamic-awaitable', dynamic_awaitable), ('dynamic-async-generator',
            dynamic_async_generator), ('non-none', lambda _event: False))):
            with self.subTest(label=label):
                provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,))
                with self.assertRaises(ctx_errors.ContextExecutionError): await self.invoke( provider,
                        self.request( session_id=f"session-{label}", operation_id=f"operation-{label}",
                            result_correlation_id=f"result-{label}",),
                        event_sink=sink,  # type: ignore[arg-type]
                    )
                self.assertEqual(provider.calls, [])

    async def test_pre_authority_observer_failure_is_primary_and_never_commits(self) -> None:
        failure = RuntimeError("PRIVATE_OBSERVER_FAILURE")
        observed: list[ctx_events.ContextExecutionEventKind] = []

        def fail_first(event: ctx_events.ContextExecutionEvent) -> None:
            observed.append(event.kind)
            if len(observed) == 1: raise failure

        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,))
        request = self.request()
        with self.assertRaises(RuntimeError) as caught:
            await self.invoke(provider, request, event_sink=fail_first)
        self.assertIs(caught.exception, failure)
        self.assertEqual(provider.calls, [])
        self.assertEqual(observed, [ctx_events.ContextExecutionEventKind.BOOTSTRAP,
            ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED])
        self.assertIsNone(self.inspect(provider, request).record)

    async def test_reference_observer_failure_aborts_and_taints_before_authority(self) -> None:
        reference = self.reference('reference-observer-operation')
        request = self.request(reference_operation_id='reference-observer-operation', mandatory_references=(reference,))
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,), reference_operation_id="reference-observer-operation")
        failure = RuntimeError("PRIVATE_REFERENCE_OBSERVER_FAILURE")
        observed: list[ctx_events.ContextExecutionEventKind] = []

        def sink(event: ctx_events.ContextExecutionEvent) -> None:
            observed.append(event.kind)
            if event.kind is ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED: raise failure

        with self.assertRaises(RuntimeError) as caught: await self.invoke(provider, request, event_sink=sink)
        self.assertIs(caught.exception, failure)
        self.assertEqual(provider.completed_references, 0)
        self.assertEqual(provider.aborted_references, [(False, False)])
        self.assertEqual(len(provider.tainted), 1)
        self.assertIn(ctx_events.ContextExecutionEventKind.TAINTED, observed)
        self.assertIs(observed[-1], ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.TAINTED)
        self.assertIsNone(inspection.record.ready_checkpoint)

    async def test_checkpoint_observer_failure_occurs_after_authoritative_commit(self) -> None:
        failure = RuntimeError("PRIVATE_TERMINAL_OBSERVER_FAILURE")
        observed: list[ctx_events.ContextExecutionEventKind] = []

        def fail_terminal(event: ctx_events.ContextExecutionEvent) -> None:
            observed.append(event.kind)
            if event.kind is ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED: raise failure

        reference = self.reference('reference-terminal-operation')
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,), reference_operation_id="reference-terminal-operation")
        request = self.request(reference_operation_id='reference-terminal-operation', mandatory_references=(reference,))
        with self.assertRaises(RuntimeError) as caught:
            await self.invoke(provider, request, event_sink=fail_terminal)
        self.assertIs(caught.exception, failure)
        self.assertIs(observed[0], ctx_events.ContextExecutionEventKind.BOOTSTRAP)
        self.assertEqual(observed[-2:], [ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED,
            ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED])
        self.assertTrue(len(observed) == 3 or (len(observed) == 4 and observed[
            1] is ctx_events.ContextExecutionEventKind.OMISSION))
        self.assertEqual(provider.tainted, [])
        self.assertEqual(provider.completed_references, 1)
        self.assertEqual(provider.aborted_references, [])
        self.assertIs(self.inspect(provider, request).record.state, owner_record_values.ContextLedgerRecordState.READY)



    async def test_authoritative_failure_remains_primary_when_terminal_sink_fails(self) -> None:
        primary = RuntimeError("PRIVATE_PROVIDER_FAILURE")

        def terminal_failure(event: ctx_events.ContextExecutionEvent) -> None:
            if event.kind is ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED:
                raise RuntimeError("PRIVATE_TERMINAL_FAILURE")

        events: list[ctx_events.ContextExecutionEventKind] = []

        def sink(event: ctx_events.ContextExecutionEvent) -> None:
            events.append(event.kind)
            terminal_failure(event)

        provider = fixture_execution_fixture.ScriptedProvider((primary,))
        with self.assertRaises(RuntimeError) as caught: await self.invoke(provider, event_sink=sink)
        self.assertIs(caught.exception, primary)
        self.assertEqual(events[-1], ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)

    async def test_failure_markers_cover_budget_action_source_answer_and_cancel(self) -> None:
        answer_failure = RuntimeError("PRIVATE_ANSWER")
        budget_service = owner_context_execution.AgentContextService(self.workspace, planning_policy=ctx_planning_inputs.ContextPlanningPolicy(
            total_source_utf8_bytes=16, p0_source_utf8_bytes=8, p1_source_utf8_bytes=8,
            p2_source_utf8_bytes=0, p3_source_utf8_bytes=0))
        cases: list[tuple[str, owner_context_execution.AgentContextService, fixture_execution_fixture.ScriptedProvider, ctx_requests.ContextInvocationRequest,
            Callable[[str, object], object] | None, ctx_events.ContextExecutionEventKind]] = [('budget',
            budget_service, fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,)), self.request(session_id='budget',
            operation_id='budget', result_correlation_id='budget'), None,
            ctx_events.ContextExecutionEventKind.BUDGET_EXHAUSTED), ('answer', self.service, fixture_execution_fixture.ScriptedProvider(
            (fixture_execution_fixture.make_answer,)), self.request(session_id='answer', operation_id='answer',
            result_correlation_id='answer'), lambda _answer, _authorization: (_ for _ in ()).throw(
            answer_failure), ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED), ('cancel',
            self.service, fixture_execution_fixture.ScriptedProvider((asyncio.CancelledError('PRIVATE_CANCEL'),)),
            self.request(session_id='cancel', operation_id='cancel', result_correlation_id='cancel'), None,
            ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)]
        for label, service, provider, request, validator, marker in cases:
            with self.subTest(label=label):
                events: list[ctx_events.ContextExecutionEvent] = []
                with mock.patch.object(fixture_execution_fixture.RecordingFinalizer, "set_validation_details",
                                       side_effect=RuntimeError("PRIVATE_DETAIL_SINK"), create=True) as retained, \
                        self.assertRaises(BaseException) as caught:
                    await self.invoke(provider, request, service=service,
                        answer_validator=validator, answer_diagnostic=lambda _answer: (
                        'PRIVATE_CALLBACK_DIAGNOSTIC',), event_sink=events.append)
                if label == "answer":
                    self.assertIs(caught.exception, answer_failure)
                    self.assertNotIn("PRIVATE_CALLBACK_DIAGNOSTIC", repr(retained.call_args_list))
                    retained.assert_called()
                    self.assertEqual(provider.finalizers[0].calls,
                                     [("rejected", ctx_provider.ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED)])
                kinds = [event.kind for event in events]
                self.assertIn(marker, kinds)
                self.assertEqual(kinds[-1], ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
                self.assertEqual(kinds.count(ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED), 1)

        self.publish()
        action_events: list[ctx_events.ContextExecutionEvent] = []
        exhausted = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_search))
        with self.assertRaises(ctx_errors.ContextExecutionError):
            await self.invoke(exhausted, self.request(session_id='action', operation_id='action',
                result_correlation_id='action'), event_sink=action_events.append)
        action_kinds = [event.kind for event in action_events]
        self.assertIn(ctx_events.ContextExecutionEventKind.ACTION_EXHAUSTED, action_kinds)
        self.assertEqual(action_kinds[-1], ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)

        source_events: list[ctx_events.ContextExecutionEvent] = []

        def drift(call: ctx_provider.ContextProviderCallRequest, active: fixture_execution_fixture.ScriptedProvider) -> str:
            path = self.workspace.root / "manuscript/part-01/chapter-01.md"
            path.write_text("PRIVATE_CHANGED_SOURCE\n", encoding="utf-8")
            return fixture_execution_fixture.make_answer(call, active)

        source_provider = fixture_execution_fixture.ScriptedProvider((drift,))
        with self.assertRaises(Exception): await self.invoke(source_provider, self.request(session_id='source',
                operation_id='source', result_correlation_id='source'), event_sink=source_events.append)
        source_kinds = [event.kind for event in source_events]
        self.assertIn(ctx_events.ContextExecutionEventKind.INVALIDATED, source_kinds)
        self.assertEqual(source_kinds[-1], ctx_events.ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
