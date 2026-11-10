'Existing ContextExecutionConcurrencyTests scenarios, moved without new cases.'
from __future__ import annotations
import asyncio
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.invocation import provider as ctx_provider
from tests.agent_context.invocation import execution_fixture as fixture_execution_fixture


class ContextExecutionConcurrencyTests(fixture_execution_fixture.ContextExecutionTestCase):
    async def test_same_scope_calls_serialize_and_second_observes_committed_delta(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_answer(call: ctx_provider.ContextProviderCallRequest, provider: fixture_execution_fixture.ScriptedProvider) -> str:
            entered.set()
            await release.wait()
            return fixture_execution_fixture.make_answer(call, provider)

        provider = fixture_execution_fixture.ScriptedProvider((blocked_answer, fixture_execution_fixture.make_answer))
        first = asyncio.create_task(self.invoke(provider, self.request(operation_id='operation-1',
            result_correlation_id='result-1')))
        await entered.wait()
        second = asyncio.create_task(self.invoke(provider, self.request(operation_id='operation-2',
            result_correlation_id='result-2')))
        await provider.second_invocation_entered.wait()
        self.assertEqual(len(provider.calls), 1)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        self.assertIs(first_result.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(second_result.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.DELTA)
        self.assertEqual(provider.calls[1].expected_thread_id, first_result.provider_thread_id)
