"""Existing BoundAgent conversation preflight and routing scenarios."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from story_writing_agents import (
    AgentReply,
    AgentRequest,
    BoundAgent,
    CHARACTER_CUSTODIAN,
    ConversationReply,
    ConversationRequest,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
)
from story_writing_agents.errors import BackendProtocolError, SessionNotFoundError

from tests.codex_native_process_fixture import _fresh_literal_object_side_effect
from tests.conversation_retrieval_wire_fixture import _RecordingRetrievalHandler


class BoundAgentConversationContractCases:
    """Conversation scenarios collected by CodexBackendTestCase."""

    async def test_converse_rejects_wrong_types_tampering_and_routing_preflight(
        self,
    ) -> None:
        backend = self._provisioned_backend('routing_session')
        definition, bound = await self._bind_definition(backend)
        manifest_path = (
            "agent_states/orchestrator_state/provider_sessions/routing_session.json"
        )
        manifest_before = self.workspace.read_json(manifest_path)

        with self.assertRaises(TypeError):
            await bound.converse(object())  # type: ignore[arg-type]

        class DerivedRequest(ConversationRequest):
            pass

        ordinary = self.conversation_request(session_id="routing_session")
        derived = DerivedRequest(
            session_id=ordinary.session_id,
            conversation_id=ordinary.conversation_id,
            turn_id=ordinary.turn_id,
            agent_id=ordinary.agent_id,
            content=ordinary.content,
            context=ordinary.context,
        )
        with self.assertRaises(TypeError):
            await bound.converse(derived)

        tampered = self.conversation_request(session_id="routing_session")
        object.__setattr__(tampered, "content", "")
        with self.assertRaises(BackendProtocolError):
            await bound.converse(tampered)

        wrong_agent = self.conversation_request(
            session_id="routing_session",
            agent_id=CHARACTER_CUSTODIAN,
        )
        with self.assertRaisesRegex(BackendProtocolError, "wrong bound agent"):
            await bound.converse(wrong_agent)

        invalid_bound = BoundAgent(
            backend,
            replace(definition, agent_id="unknown_agent"),
            bound.staging_directory,
        )
        with self.assertRaisesRegex(BackendProtocolError, "definition"):
            await invalid_bound.converse(ordinary)

        with self.assertRaises(SessionNotFoundError):
            await bound.converse(
                self.conversation_request(session_id="missing_session")
            )

        self.assertEqual(self.provider_invocations(), [])
        self.assertEqual(self.workspace.read_json(manifest_path), manifest_before)

    async def test_retrieval_whole_loop_serializes_with_legacy_converse_and_invoke(
        self,
    ) -> None:
        session_id = "retrieval_lock_session"
        backend = self._provisioned_backend(session_id)
        definition = self.definition()
        retrieval_bound = await backend.bind(definition)
        conversation_bound = await backend.bind(definition)
        debate_bound = await backend.bind(definition)
        entered = asyncio.Event()
        release = asyncio.Event()
        trace: list[str] = []

        async def retrieval_call(
            _bound: BoundAgent,
            request: ConversationRetrievalRequest,
            _handler: object,
            *,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> ConversationRetrievalReply:
            """Hold the retrieval operation inside the shared thread lock."""

            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            trace.append("retrieval_enter")
            entered.set()
            await release.wait()
            trace.append("retrieval_exit")
            return ConversationRetrievalReply(
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                content="retrieval",
                sources=(),
            )

        async def conversation_call(
            _bound: BoundAgent,
            request: ConversationRequest,
            *,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> ConversationReply:
            """Record legacy conversation entry after lock acquisition."""

            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            trace.append("conversation_enter")
            return ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                "conversation",
            )

        async def invoke_call(
            _bound: BoundAgent,
            request: AgentRequest,
            *,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> AgentReply:
            """Record debate entry after lock acquisition."""

            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            trace.append("invoke_enter")
            return AgentReply.candidate(request.revision_id, "candidate")

        handler = _RecordingRetrievalHandler()
        with (
            _fresh_literal_object_side_effect(backend, '_converse_with_retrieval_codex', retrieval_call),
            _fresh_literal_object_side_effect(backend, '_converse_codex', conversation_call),
            _fresh_literal_object_side_effect(backend, '_invoke_codex', invoke_call),
        ):
            retrieval_task = asyncio.create_task(
                retrieval_bound.converse_with_retrieval(
                    self.retrieval_request(session_id=session_id),
                    handler,
                )
            )
            await entered.wait()
            conversation_task = asyncio.create_task(
                conversation_bound.converse(
                    self.conversation_request(session_id=session_id)
                )
            )
            invoke_task = asyncio.create_task(
                debate_bound.invoke(self.request(session_id=session_id))
            )
            await asyncio.sleep(0)
            self.assertEqual(trace, ["retrieval_enter"])
            release.set()
            await asyncio.gather(retrieval_task, conversation_task, invoke_task)

        self.assertEqual(trace[0:2], ["retrieval_enter", "retrieval_exit"])
        self.assertEqual(set(trace[2:]), {"conversation_enter", "invoke_enter"})
