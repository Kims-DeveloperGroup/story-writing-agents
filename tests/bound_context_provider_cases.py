"""Existing runtime case for the bound context provider."""

from __future__ import annotations

from unittest import mock

from story_writing_agents import PLOT_ARCHITECT
from story_writing_agents.agent_context import ProviderCallKind, ProviderCallState
from story_writing_agents.agent_context.invocation import provider as ctx_provider
from story_writing_agents.agent_context.manifest import ContextScope, ContextSubjectKind
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.provider_adapter import (
    ProviderInvocationError,
    ProviderResult,
    record_provider_failure_accounting,
)


class BoundContextProviderCaseDefinitions:
    async def test_contextual_submitted_failure_taints_only_exact_context_thread(
        self,
    ) -> None:
        session_id = "contextual_failure"
        backend, bound = await self._provisioned_bound(session_id)
        provider = bound._context_provider()
        scope = ContextScope(
            "workspace-context",
            session_id,
            ContextSubjectKind.DEBATE,
            "debate-context",
            PLOT_ARCHITECT,
        )
        policy = "sha256:" + "8" * 64
        schema = {"type": "object"}
        payload = {"content": "PRIVATE_CONTEXT_FAILURE"}
        success = ProviderResult("context-before-failure", "{}")
        submitted = ProviderInvocationError(
            "fixed contextual failure",
            taint_existing_thread=True,
        )
        record_provider_failure_accounting(
            submitted,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=13,
        )
        adapter = mock.AsyncMock(side_effect=(success, submitted))
        with mock.patch.object(backend._codex_adapter, "invoke", new=adapter):
            async with provider.invocation(scope, policy):
                first = await provider.invoke_context(
                    ctx_provider.ContextProviderCallRequest(
                        scope,
                        policy,
                        "context-success",
                        ProviderCallKind.DEBATE,
                        schema,
                        payload,
                    )
                )
                with self.assertRaises(BackendProtocolError) as caught:
                    await provider.invoke_context(
                        ctx_provider.ContextProviderCallRequest(
                            scope,
                            policy,
                            "context-failure",
                            ProviderCallKind.DEBATE,
                            schema,
                            payload,
                            expected_thread_id=first.thread_id,
                        )
                    )
                facts = ctx_provider.context_provider_failure_facts(caught.exception)
                self.assertIs(facts.accounting.state, ProviderCallState.SUBMITTED)
                self.assertIs(facts.taint_existing_thread, True)
                self.assertIsNone(provider.inspect_context_thread(scope, policy))
        self.assertEqual(
            self.workspace.read_json(
                f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
            )["provider_threads"],
            {},
        )
