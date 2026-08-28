"""End-to-end acceptance coverage for contextual story-runtime adoption."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from unittest import mock

from story_writing_agents import (
    AGENT_SPECS,
    AgentBackend,
    BoundAgent,
    CHARACTER_CUSTODIAN,
    DebateStatus,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
    StoryRuntime,
    Workspace,
)
from story_writing_agents.agent_context import (
    ProviderCallKind,
    ProviderCallState,
)
from story_writing_agents.agent_context.ledger import (
    ContextLedger,
    ContextLedgerKey,
    ContextLedgerRecordState,
)
from story_writing_agents.agent_context.manifest import ContextSubjectKind
from story_writing_agents.agent_context.service import AgentContextService
from story_writing_agents.agent_context.sources import writing_artifact_source_id
from story_writing_agents.conversation_retrieval import (
    ConversationRagAttemptOutcome,
    ConversationRagContext,
    ConversationRagExcerpt,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalSource,
)
from story_writing_agents.conversation_rag import (
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagOutcome,
    ConversationRagReason,
    ConversationRagSourceReference,
)
from story_writing_agents.conversation_rag_runtime import (
    ConversationRagRuntimeService,
)
from story_writing_agents.conversation_search import (
    ConversationCitation,
    ConversationMemoryService,
    ConversationSearchScope,
)
from story_writing_agents.conversation_storage import (
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import (
    ConversationEventAppend,
    ConversationEventKind,
)
from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH, WorkspaceDebugLog
from story_writing_agents.errors import (
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
)
from story_writing_agents.models import AgentStateEntry
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderInvocation,
    ProviderResult,
    record_provider_failure_accounting,
)
from story_writing_agents.writing_artifacts import (
    ReferenceReadActionKind,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import initialized_workspace


SearchSelection = tuple[tuple[str, ...], str]


def _framed_observation(value: object) -> dict[str, Any]:
    """Return the single JSON frame from one provider observation string."""

    if not isinstance(value, str):
        raise AssertionError("context continuation did not contain an observation")
    frames: list[dict[str, Any]] = []
    for line in value.splitlines():
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            frames.append(decoded)
    if not frames:
        raise AssertionError("context observation did not contain a JSON frame")
    return frames[-1]


def _projected_source_ids(payload: Mapping[str, object]) -> tuple[str, ...]:
    result: list[str] = []
    units = payload.get("projected_units", ())
    if not isinstance(units, list):
        return ()
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        sources = unit.get("sources", ())
        if not isinstance(sources, list):
            continue
        for source in sources:
            if isinstance(source, Mapping) and isinstance(
                source.get("source_id"), str
            ):
                result.append(source["source_id"])
    return tuple(result)


def _current_material(payload: Mapping[str, object]) -> dict[str, Any]:
    units = payload.get("projected_units")
    if not isinstance(units, list):
        raise AssertionError("initial context call omitted projected units")
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        sources = unit.get("sources")
        if not isinstance(sources, list):
            continue
        if any(
            isinstance(source, Mapping)
            and source.get("source_id") == "runtime_input:current"
            for source in sources
        ):
            content = unit.get("content")
            if not isinstance(content, str):
                raise AssertionError("current context material was not text")
            value = json.loads(content)
            if not isinstance(value, dict):
                raise AssertionError("current context material was not an object")
            return value
    raise AssertionError("initial context call omitted current request material")


class _ContextAdapterHarness:
    """Answer real backend contextual calls while retaining exact wire facts."""

    def __init__(self) -> None:
        self.invocations: list[ProviderInvocation] = []
        self.bindings: list[ProviderBinding] = []
        self.context_payloads: list[Mapping[str, object]] = []
        self.reference_payloads: list[Mapping[str, object]] = []
        self.search_frames: list[dict[str, Any]] = []
        self.read_frames: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.current_by_operation: dict[str, dict[str, Any]] = {}
        self.search_selector: Callable[[dict[str, Any]], SearchSelection | None] = (
            lambda _current: None
        )
        self.invalid_domain_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.invalid_citation_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.provider_failure_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.exhaust_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.search_only_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.block_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.reference_failure = False
        self.mutate_before_answer: Callable[[dict[str, Any]], None] | None = None
        self.conflicts: set[tuple[str, str, str]] = set()
        self.final_updates: dict[str, dict[str, str]] = {}
        self.candidates: dict[str, str] = {}
        self.block_entered = asyncio.Event()
        self.block_release = asyncio.Event()
        self._next_thread = 1

    @staticmethod
    def _correlation(payload: Mapping[str, object]) -> dict[str, Any]:
        correlation = payload.get("correlation")
        if not isinstance(correlation, Mapping):
            raise AssertionError("context call omitted correlation")
        return dict(correlation)

    def _thread_id(self, invocation: ProviderInvocation) -> str:
        if invocation.thread_id is not None:
            return invocation.thread_id
        value = f"context-thread-{self._next_thread}"
        self._next_thread += 1
        return value

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _domain_answer(
        self,
        current: dict[str, Any],
        correlation: Mapping[str, object],
        used_handles: tuple[str, ...],
    ) -> str:
        workflow = current["workflow"]
        identity = current["identity"]
        if workflow == "conversation":
            domain: object = {
                "session_id": identity["session_id"],
                "conversation_id": identity["conversation_id"],
                "turn_id": identity["turn_id"],
                "content": f"contextual reply: {current['command']}",
            }
        elif workflow == "conversation_retrieval":
            rag_context = current.get("rag_context")
            rag_ids: list[str] = []
            if isinstance(rag_context, Mapping):
                excerpts = rag_context.get("excerpts", ())
                if isinstance(excerpts, list):
                    rag_ids = [
                        item["citation_id"]
                        for item in excerpts
                        if isinstance(item, Mapping)
                        and isinstance(item.get("citation_id"), str)
                    ]
            context_ids = [f"r{index}" for index in range(1, len(used_handles) + 1)]
            if self.invalid_citation_selector(current):
                context_ids = ["r999"]
            domain = {
                "action": {
                    "kind": "answer",
                    "session_id": identity["session_id"],
                    "conversation_id": identity["conversation_id"],
                    "turn_id": identity["turn_id"],
                    "action_index": correlation["action_index"],
                    "content": f"retrieved reply: {current['command']}",
                    "citation_ids": [*rag_ids, *context_ids],
                }
            }
        else:
            debate_id = identity["debate_id"]
            revision = current["revision"]
            phase = current["phase"]
            agent_id = identity["agent_id"]
            if phase == "audit":
                conflict = (debate_id, revision, agent_id) in self.conflicts
                domain = {
                    "revision_id": revision,
                    "verdict": "conflict" if conflict else "approve",
                    "content": "conflict requires revision" if conflict else "",
                }
            elif phase == "finalize":
                domain = {
                    "revision_id": revision,
                    "core_updates": [
                        {"filename": filename, "content": content}
                        for filename, content in self.final_updates.get(
                            debate_id, {}
                        ).items()
                    ],
                }
            else:
                content = self.candidates.get(
                    debate_id,
                    f"candidate:{debate_id}:{revision}:{phase}",
                )
                domain = {"revision_id": revision, "content": content}
        if self.invalid_domain_selector(current):
            return "not-domain-json"
        return self._json(domain)

    def _answer_action(
        self,
        current: dict[str, Any],
        correlation: dict[str, Any],
        used_handles: tuple[str, ...] = (),
    ) -> str:
        if self.mutate_before_answer is not None:
            mutation = self.mutate_before_answer
            self.mutate_before_answer = None
            mutation(current)
        action = {
            "action": "answer",
            "content": self._domain_answer(current, correlation, used_handles),
            "correlation": correlation,
            "used_handles": list(used_handles),
        }
        self.answers.append(action)
        return self._json(action)

    async def invoke(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        """Return one exact provider result for the real backend call seam."""

        self.invocations.append(invocation)
        self.bindings.append(binding)
        payload = invocation.payload
        if invocation.call_kind is ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ:
            self.reference_payloads.append(payload)
            if self.reference_failure:
                response = self._json({"action": {"kind": "invalid"}})
            else:
                response = self._json(
                    {
                        "action": {
                            "kind": (
                                ReferenceReadActionKind.READ_WRITING_ARTIFACTS.value
                            ),
                            "correlation": payload["correlation"],
                            "references": payload["references"],
                        }
                    }
                )
            return ProviderResult(
                self._thread_id(invocation),
                response,
                actual_transport_utf8_bytes=len(invocation.payload_json or b""),
            )

        correlation = self._correlation(payload)
        operation_id = correlation["operation_id"]
        if not isinstance(operation_id, str):
            raise AssertionError("context operation ID was not text")
        if payload.get("operation") == "agent_context_v1":
            self.context_payloads.append(payload)
            current = _current_material(payload)
            self.current_by_operation[operation_id] = current
            if self.block_selector(current):
                self.block_entered.set()
                await self.block_release.wait()
            if self.provider_failure_selector(current):
                failure = RuntimeError("simulated contextual provider failure")
                record_provider_failure_accounting(
                    failure,
                    state=ProviderCallState.SUBMITTED,
                    actual_transport_utf8_bytes=len(invocation.payload_json or b""),
                )
                raise failure
            search = self.search_selector(current)
            if search is None:
                response = self._answer_action(current, correlation)
            else:
                source_kinds, text = search
                response = self._json(
                    {
                        "action": "search",
                        "correlation": correlation,
                        "queries": [
                            {"source_kinds": list(source_kinds), "text": text}
                        ],
                    }
                )
        else:
            current = self.current_by_operation[operation_id]
            frame = _framed_observation(payload.get("observation"))
            if "hits" in frame:
                self.search_frames.append(frame)
                handles = tuple(
                    item["handle"]
                    for item in frame["hits"]
                    if isinstance(item, Mapping)
                    and isinstance(item.get("handle"), str)
                )
                if not handles:
                    response = self._answer_action(current, correlation)
                elif self.search_only_selector(current):
                    selection = self.search_selector(current)
                    if selection is None:
                        raise AssertionError("search-only script requires a search")
                    source_kinds, text = selection
                    response = self._json(
                        {
                            "action": "search",
                            "correlation": correlation,
                            "queries": [
                                {"source_kinds": list(source_kinds), "text": text}
                            ],
                        }
                    )
                else:
                    response = self._json(
                        {
                            "action": "read",
                            "correlation": correlation,
                            "handles": list(handles),
                        }
                    )
            elif "evidence" in frame:
                self.read_frames.append(frame)
                handles = tuple(
                    item["handle"]
                    for item in frame["evidence"]
                    if isinstance(item, Mapping)
                    and isinstance(item.get("handle"), str)
                )
                if self.exhaust_selector(current):
                    selection = self.search_selector(current)
                    if selection is None:
                        raise AssertionError("exhaustion script requires a search")
                    source_kinds, text = selection
                    response = self._json(
                        {
                            "action": "search",
                            "correlation": correlation,
                            "queries": [
                                {"source_kinds": list(source_kinds), "text": text}
                            ],
                        }
                    )
                else:
                    response = self._answer_action(current, correlation, handles)
            else:
                raise AssertionError("unexpected contextual observation")
        return ProviderResult(
            self._thread_id(invocation),
            response,
            actual_transport_utf8_bytes=len(invocation.payload_json or b""),
        )


class ContextualRuntimeAdoptionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the public runtime through the real contextual service contract."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.services: ConversationStorageServices = (
            compose_filesystem_conversation_storage(self.workspace)
        )
        self.backend = AgentBackend(self.workspace, executable=sys.executable)
        self.addCleanup(self.backend.close)
        self.provider = _ContextAdapterHarness()
        adapter_patch = mock.patch.object(
            self.backend._codex_adapter,
            "invoke",
            side_effect=self.provider.invoke,
        )
        adapter_patch.start()
        self.addCleanup(adapter_patch.stop)
        self.runtime = StoryRuntime(
            self.workspace,
            self.backend,
            conversation_storage_services=self.services,
        )

    def ready(self, session_id: str) -> None:
        self.backend.create_session_with_access(session_id)

    def conversation(self, session_id: str, subject: str = "Test conversation") -> str:
        return self.services.catalog.create(session_id, subject).conversation_id

    def append_turn(
        self,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        user_text: str,
        reply_text: str,
        *,
        recipient: str = PLOT_ARCHITECT,
        outcome: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
    ) -> None:
        self.services.event_store.append(
            ConversationEventAppend(
                session_id,
                conversation_id,
                turn_id,
                "user",
                recipient,
                ConversationEventKind.USER_MESSAGE,
                user_text,
            )
        )
        self.services.event_store.append(
            ConversationEventAppend(
                session_id,
                conversation_id,
                turn_id,
                (
                    recipient
                    if outcome is ConversationEventKind.AGENT_REPLY
                    else "runtime"
                ),
                "user",
                outcome,
                reply_text,
            )
        )

    def context_debug_records(self, session_id: str) -> list[dict[str, Any]]:
        path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        if not path.exists():
            return []
        return [
            value
            for value in (
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            )
            if value.get("operation") == "agent_context"
            and value.get("session_id") == session_id
        ]

    @staticmethod
    def source_id(filename: str) -> str:
        return writing_artifact_source_id(f"core_documents/{filename}")

    async def test_raw_workspace_first_turns_and_late_role_core_are_contextual(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="raw-context-runtime-")
        self.addCleanup(temporary.cleanup)
        workspace = Workspace(Path(temporary.name) / "workspace").initialize()
        services = compose_filesystem_conversation_storage(workspace)
        backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(backend.close)
        provider = _ContextAdapterHarness()
        adapter_patch = mock.patch.object(
            backend._codex_adapter,
            "invoke",
            side_effect=provider.invoke,
        )
        adapter_patch.start()
        self.addCleanup(adapter_patch.stop)
        runtime = StoryRuntime(
            workspace,
            backend,
            conversation_storage_services=services,
        )
        session_id = "raw-first-turns"
        backend.create_session_with_access(session_id)
        ordinary_id = services.catalog.create(
            session_id,
            "Fresh ordinary",
        ).conversation_id
        retrieval_id = services.catalog.create(
            session_id,
            "Fresh retrieval",
        ).conversation_id

        ordinary = await runtime.converse(
            session_id,
            ordinary_id,
            PLOT_ARCHITECT,
            "first ordinary request",
        )
        retrieval = await runtime.converse_with_retrieval(
            session_id,
            retrieval_id,
            PLOT_ARCHITECT,
            "first retrieval request",
            memory_service=ConversationMemoryService(
                ConversationSearchScope(session_id),
                services.catalog,
                services.event_store,
            ),
        )
        self.assertEqual(ordinary.content, "contextual reply: first ordinary request")
        self.assertEqual(retrieval.content, "retrieved reply: first retrieval request")
        self.assertEqual(len(provider.context_payloads), 2)

        absent_core_ids = {
            writing_artifact_source_id(f"core_documents/{filename}")
            for filename in AGENT_SPECS[PLOT_ARCHITECT].context_documents
        }
        for payload in provider.context_payloads:
            available_ids = {
                item["source_id"] for item in payload["available_sources"]
            }
            projected_ids = set(_projected_source_ids(payload))
            self.assertTrue(absent_core_ids.isdisjoint(available_ids))
            self.assertTrue(absent_core_ids.isdisjoint(projected_ids))

        for payload, conversation_id, pending_text in (
            (
                provider.context_payloads[0],
                ordinary_id,
                "first ordinary request",
            ),
            (
                provider.context_payloads[1],
                retrieval_id,
                "first retrieval request",
            ),
        ):
            source_id = f"conversation:{conversation_id}"
            self.assertIn(
                source_id,
                {item["source_id"] for item in payload["available_sources"]},
            )
            unit = next(
                unit
                for unit in payload["projected_units"]
                if source_id
                in {
                    source["source_id"] for source in unit["sources"]
                }
            )
            self.assertEqual(json.loads(unit["content"])["pairs"], [])
            self.assertNotIn(pending_text, unit["content"])

        self.assertEqual(
            tuple(
                event.kind
                for event in services.event_store.read(session_id, ordinary_id)
            ),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(
            tuple(
                event.kind
                for event in services.event_store.read(session_id, retrieval_id)
            ),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )

        core_id = writing_artifact_source_id(
            "core_documents/global_outline.md"
        )
        WritingArtifactRepository(workspace.root).commit_consensus(
            output_path="part-01/raw-context.md",
            candidate="raw context publication",
            core_updates={"global_outline.md": "LATE_ROLE_CORE_MARKER"},
            session_id=session_id,
            debate_id="raw-context-publication",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )
        follow_up = await runtime.converse(
            session_id,
            ordinary_id,
            PLOT_ARCHITECT,
            "ordinary after core creation",
        )
        self.assertEqual(
            follow_up.content,
            "contextual reply: ordinary after core creation",
        )
        delta = provider.context_payloads[2]
        self.assertEqual(delta["delivery_mode"], "delta")
        core_entry = next(
            item for item in delta["available_sources"]
            if item["source_id"] == core_id
        )
        self.assertEqual(core_entry["priority"], "p2_working")
        self.assertEqual(core_entry["semantic_channel"], "hard_constraint")
        self.assertIn(core_id, _projected_source_ids(delta))
        self.assertIn(
            "LATE_ROLE_CORE_MARKER",
            "\n".join(
                unit["content"]
                for unit in delta["projected_units"]
                if core_id
                in {
                    source["source_id"] for source in unit["sources"]
                }
            ),
        )
        scope = AgentContextService(workspace).scope(
            session_id,
            ContextSubjectKind.CONVERSATION,
            ordinary_id,
            PLOT_ARCHITECT,
        )
        inspection = ContextLedger(workspace).inspect(
            ContextLedgerKey(scope, "codex")
        )
        self.assertIs(inspection.record.state, ContextLedgerRecordState.READY)
        self.assertEqual(
            len(services.event_store.read(session_id, ordinary_id)),
            4,
        )

    async def test_four_roles_bootstrap_delta_core_refresh_and_scope_switch(
        self,
    ) -> None:
        session_id = "context_role_matrix"
        self.ready(session_id)

        for role_index, agent_id in enumerate(ROSTER, 1):
            conversation_id = self.conversation(session_id, f"Role {agent_id}")
            first = await self.runtime.converse(
                session_id,
                conversation_id,
                agent_id,
                f"fresh request {agent_id}",
            )
            first_payload = self.provider.context_payloads[-1]
            expected_core_ids = tuple(
                self.source_id(filename)
                for filename in AGENT_SPECS[agent_id].context_documents
            )
            self.assertEqual(first_payload["delivery_mode"], "bootstrap")
            self.assertTrue(
                set(expected_core_ids).issubset(_projected_source_ids(first_payload))
            )
            self.assertEqual(
                self.provider.bindings[-1].definition.context_document_files,
                (),
            )
            self.assertIn("contextual reply", first.content)

            second = await self.runtime.converse(
                session_id,
                conversation_id,
                agent_id,
                f"follow-up request {agent_id}",
            )
            second_payload = self.provider.context_payloads[-1]
            self.assertEqual(second_payload["delivery_mode"], "delta")
            self.assertFalse(
                set(expected_core_ids).issubset(_projected_source_ids(second_payload))
            )
            self.assertIn("contextual reply", second.content)

            changed_filename = AGENT_SPECS[agent_id].context_documents[0]
            changed_token = f"changed-core-{role_index}-{agent_id}"
            self.workspace.path(f"core_documents/{changed_filename}").write_text(
                changed_token,
                encoding="utf-8",
            )
            await self.runtime.converse(
                session_id,
                conversation_id,
                agent_id,
                f"core refresh {agent_id}",
            )
            changed_payload = self.provider.context_payloads[-1]
            self.assertEqual(changed_payload["delivery_mode"], "delta")
            self.assertIn(
                self.source_id(changed_filename),
                _projected_source_ids(changed_payload),
            )
            self.assertTrue(
                any(
                    isinstance(unit, Mapping)
                    and isinstance(unit.get("content"), str)
                    and changed_token in unit["content"]
                    for unit in changed_payload["projected_units"]
                )
            )

            switched_conversation = self.conversation(
                session_id, f"Switched {agent_id}"
            )
            await self.runtime.converse(
                session_id,
                switched_conversation,
                agent_id,
                f"scope switch {agent_id}",
            )
            self.assertEqual(
                self.provider.context_payloads[-1]["delivery_mode"],
                "bootstrap",
            )

        fingerprints: dict[str, set[str]] = {agent_id: set() for agent_id in ROSTER}
        for binding in self.provider.bindings:
            fingerprints[binding.definition.agent_id].add(
                binding.definition.fingerprint
            )
        self.assertEqual(
            {agent_id: len(values) for agent_id, values in fingerprints.items()},
            {agent_id: 1 for agent_id in ROSTER},
        )

    async def test_conversation_failures_privacy_and_fail_open_events(
        self,
    ) -> None:
        session_id = "context_conversation"
        self.ready(session_id)
        conversation_id = self.conversation(session_id)
        foreign_private = "FOREIGN_CHARACTER_PRIVATE_STATE"
        self.workspace.append_agent_state(
            AgentStateEntry(
                CHARACTER_CUSTODIAN,
                session_id,
                "private_debate",
                "audit",
                0,
                "r0",
                "private",
                foreign_private,
                "2026-08-29T00:00:00+00:00",
            )
        )

        with mock.patch.object(
            BoundAgent,
            "converse",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError("legacy conversation route selected"),
        ) as legacy_converse:
            first = await self.runtime.converse(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "PRIVATE_VISIBLE_FIRST_COMMAND",
            )
        legacy_converse.assert_not_awaited()
        self.assertEqual(first.agent_id, PLOT_ARCHITECT)
        events = self.services.event_store.read(session_id, conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        available_ids = tuple(
            item["source_id"]
            for item in self.provider.context_payloads[-1]["available_sources"]
        )
        self.assertIn(f"own_agent_state:{PLOT_ARCHITECT}", available_ids)
        self.assertNotIn(f"own_agent_state:{CHARACTER_CUSTODIAN}", available_ids)
        self.assertNotIn(
            foreign_private.encode("utf-8"),
            b"".join(
                invocation.payload_json or b""
                for invocation in self.provider.invocations
            ),
        )

        direct_outcomes = [
            record["outcome"]
            for record in self.context_debug_records(session_id)
            if record.get("outcome") is not None
        ]
        self.assertLess(
            direct_outcomes.index("context_bootstrap"),
            direct_outcomes.index("context_answer_authorized"),
        )
        self.assertLess(
            direct_outcomes.index("context_answer_authorized"),
            direct_outcomes.index("context_checkpoint_advanced"),
        )

        with mock.patch.object(
            WorkspaceDebugLog,
            "trace",
            side_effect=RuntimeError("diagnostic sink unavailable"),
        ):
            second = await self.runtime.converse(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "diagnostic fail-open follow-up",
            )
        self.assertIn("contextual reply", second.content)
        self.assertEqual(
            self.provider.context_payloads[-1]["delivery_mode"], "delta"
        )

        self.provider.provider_failure_selector = lambda current: (
            current["command"] == "provider failure turn"
        )
        with self.assertRaisesRegex(RuntimeError, "contextual provider failure"):
            await self.runtime.converse(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "provider failure turn",
            )
        failed_events = self.services.event_store.read(session_id, conversation_id)
        self.assertIs(failed_events[-1].kind, ConversationEventKind.RUNTIME_ERROR)
        self.provider.provider_failure_selector = lambda _current: False
        recovered = await self.runtime.converse(
            session_id,
            conversation_id,
            PLOT_ARCHITECT,
            "recover after provider failure",
        )
        self.assertIn("contextual reply", recovered.content)
        self.assertEqual(
            self.provider.context_payloads[-1]["delivery_mode"], "bootstrap"
        )

        self.provider.block_selector = lambda current: (
            current["command"] == "cancel this turn"
        )
        cancelled = asyncio.create_task(
            self.runtime.converse(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "cancel this turn",
            )
        )
        await self.provider.block_entered.wait()
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        cancelled_events = self.services.event_store.read(
            session_id, conversation_id
        )
        self.assertIs(cancelled_events[-1].kind, ConversationEventKind.RUNTIME_ERROR)

        debug_bytes = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_bytes()
        for private in (
            foreign_private,
            "PRIVATE_VISIBLE_FIRST_COMMAND",
            first.content,
        ):
            self.assertNotIn(private.encode("utf-8"), debug_bytes)
        allowed_debug_fields = {
            "schema_version",
            "timestamp",
            "level",
            "component",
            "operation",
            "phase",
            "session_id",
            "conversation_id",
            "debate_id",
            "agent_id",
            "outcome",
            "exception",
        }
        self.assertTrue(
            all(
                set(record).issubset(allowed_debug_fields)
                for record in self.context_debug_records(session_id)
            )
        )

    async def test_retrieval_reads_complete_same_session_turns_with_progress(
        self,
    ) -> None:
        session_id = "context_retrieval"
        foreign_session = "foreign_retrieval"
        self.ready(session_id)
        selected_id = self.conversation(session_id, "Selected")
        source_a = self.conversation(session_id, "Source A")
        source_b = self.conversation(session_id, "Source B")
        foreign_id = self.conversation(foreign_session, "Foreign")
        self.append_turn(
            session_id,
            source_a,
            "source_a_complete",
            "shared-needle alpha question",
            "alpha complete reply",
        )
        self.append_turn(
            session_id,
            source_b,
            "source_b_complete",
            "shared-needle beta question",
            "beta complete reply",
        )
        self.services.event_store.append(
            ConversationEventAppend(
                session_id,
                source_a,
                "source_a_incomplete",
                "user",
                PLOT_ARCHITECT,
                ConversationEventKind.USER_MESSAGE,
                "shared-needle INCOMPLETE_PRIVATE_TURN",
            )
        )
        self.append_turn(
            session_id,
            source_b,
            "source_b_error",
            "shared-needle ERROR_PRIVATE_TURN",
            "runtime failure",
            outcome=ConversationEventKind.RUNTIME_ERROR,
        )
        self.append_turn(
            foreign_session,
            foreign_id,
            "foreign_complete",
            "shared-needle FOREIGN_PRIVATE_TURN",
            "foreign reply",
        )
        memory = ConversationMemoryService(
            ConversationSearchScope(session_id),
            self.services.catalog,
            self.services.event_store,
        )
        self.provider.search_selector = lambda current: (
            (("conversation",), "shared-needle")
            if current["workflow"] == "conversation_retrieval"
            else None
        )
        progress: list[object] = []

        with mock.patch.object(
            BoundAgent,
            "converse_with_retrieval",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError("legacy retrieval route selected"),
        ) as legacy_retrieval:
            result = await self.runtime.converse_with_retrieval(
                session_id,
                selected_id,
                PLOT_ARCHITECT,
                "retrieve stored evidence",
                memory_service=memory,
                progress_sink=progress.append,
            )
        legacy_retrieval.assert_not_awaited()
        self.assertEqual(len(self.provider.search_frames), 1)
        self.assertEqual(len(self.provider.read_frames), 1)
        read_frame = self.provider.read_frames[0]
        read_wire = json.dumps(read_frame, ensure_ascii=False)
        self.assertIn("alpha complete reply", read_wire)
        self.assertIn("beta complete reply", read_wire)
        for forbidden in (
            "INCOMPLETE_PRIVATE_TURN",
            "ERROR_PRIVATE_TURN",
            "FOREIGN_PRIVATE_TURN",
        ):
            self.assertNotIn(forbidden, read_wire)

        expected_conversations: list[str] = []
        for evidence in read_frame["evidence"]:
            locator = evidence["provenance"]["locator"]
            conversation_id = locator.removeprefix("conversation/")
            if conversation_id not in expected_conversations:
                expected_conversations.append(conversation_id)
        self.assertEqual(
            tuple(source.citation.conversation_id for source in result.sources),
            tuple(expected_conversations),
        )
        self.assertEqual(
            self.provider.answers[-1]["used_handles"],
            [item["handle"] for item in read_frame["evidence"]],
        )
        self.assertEqual(
            [event.phase for event in progress],
            [
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                ConversationRetrievalProgressPhase.READ_STARTED,
                ConversationRetrievalProgressPhase.READ_COMPLETED,
            ],
        )
        self.assertTrue(all(event.action_index > 0 for event in progress))
        self.assertTrue(all(event.session_id == session_id for event in progress))

        observer_conversation = self.conversation(session_id, "Observer failure")

        def reject_progress(event: object) -> None:
            if event.phase is ConversationRetrievalProgressPhase.SEARCH_STARTED:
                raise RuntimeError("observer rejected progress")

        with self.assertRaises(ConversationRetrievalObserverError):
            await self.runtime.converse_with_retrieval(
                session_id,
                observer_conversation,
                PLOT_ARCHITECT,
                "observer failure request",
                memory_service=memory,
                progress_sink=reject_progress,
            )
        observer_events = self.services.event_store.read(
            session_id, observer_conversation
        )
        self.assertEqual(
            tuple(event.kind for event in observer_events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )

    async def test_contextual_retrieval_preserves_mixed_rag_and_read_citations(
        self,
    ) -> None:
        session_id = "context_mixed_rag"
        self.ready(session_id)
        selected = self.conversation(session_id, "Selected RAG")
        source_metadata = self.services.catalog.create(session_id, "RAG source")
        source = source_metadata.conversation_id
        turn_id = "rag_source_turn"
        self.append_turn(
            session_id,
            source,
            turn_id,
            "LEXICAL_RAG_SHARED_NEEDLE",
            "RAG and lexical source reply",
        )
        content = "authoritative semantic excerpt"
        document = ConversationRagDocumentIdentity(
            key=ConversationRagDocumentKey(session_id, "1" * 64),
            conversation_id=source,
            start_turn_id=turn_id,
            end_turn_id=turn_id,
            start_sequence=1,
            end_sequence=2,
            revision_id="sha256:" + "1" * 64,
        )
        rag_source = ConversationRetrievalSource(
            subject=source_metadata.subject,
            citation=ConversationCitation(session_id, source, None, None, None),
        )
        excerpt = ConversationRagExcerpt(
            citation_id="g1",
            source=rag_source,
            reference=ConversationRagSourceReference(
                document,
                "fragment-1",
                0,
                len(content),
            ),
            content=content,
        )
        rag_context = ConversationRagContext(
            outcome=ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.COMPLETE,
                reason=ConversationRagReason.NONE,
            ),
            excerpts=(excerpt,),
            retained_utf8_bytes=len(content.encode("utf-8")),
            truncated=False,
        )

        class UnusedRagProvider:
            @staticmethod
            def fail(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("patched RAG path called its provider")

            project_documents = fail
            rebuild_projection = fail
            retrieve_semantic = fail
            retrieve_hybrid = fail

        class UnusedRagState:
            @staticmethod
            def inspect_projection_state(*_args: object) -> object:
                raise AssertionError("patched RAG path inspected state")

            @staticmethod
            def mark_projection_stale(*_args: object) -> object:
                raise AssertionError("patched RAG path marked state")

        rag_service = ConversationRagRuntimeService(
            UnusedRagProvider(),
            UnusedRagState(),
        )
        runtime = StoryRuntime(
            self.workspace,
            self.backend,
            conversation_storage_services=self.services,
            conversation_rag_service=rag_service,
        )
        memory = ConversationMemoryService(
            ConversationSearchScope(session_id),
            self.services.catalog,
            self.services.event_store,
        )
        self.provider.search_selector = lambda current: (
            (("conversation",), "LEXICAL_RAG_SHARED_NEEDLE")
            if current.get("workflow") == "conversation_retrieval"
            else None
        )
        with (
            mock.patch.object(
                ConversationRagRuntimeService,
                "preflight",
                return_value=rag_context,
            ) as preflight,
            mock.patch.object(
                ConversationRagRuntimeService,
                "project_authorized",
            ) as projection,
        ):
            result = await runtime.converse_with_retrieval(
                session_id,
                selected,
                PLOT_ARCHITECT,
                "combine semantic and exact context",
                memory_service=memory,
                rag_query="semantic query",
            )
        preflight.assert_called_once()
        projection.assert_called_once()
        self.assertEqual(result.rag_outcome, rag_context.outcome)
        self.assertEqual(result.rag_sources, (rag_source,))
        self.assertEqual(result.sources[0], rag_source)
        self.assertEqual(result.sources[1].citation.conversation_id, source)
        self.assertEqual(
            json.loads(self.provider.answers[-1]["content"])["action"][
                "citation_ids"
            ],
            ["g1", "r1"],
        )

    async def test_debate_all_phases_multiple_rounds_and_publication(
        self,
    ) -> None:
        session_id = "context_debate_consensus"
        debate_id = "multi_round_context"
        self.ready(session_id)
        self.provider.conflicts.update(
            {
                (debate_id, "r0", PLOT_ARCHITECT),
                (debate_id, "r0", CHARACTER_CUSTODIAN),
                (debate_id, "r1", EXECUTION_SPECIALIST),
            }
        )

        with mock.patch.object(
            BoundAgent,
            "invoke",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError("legacy debate route selected"),
        ) as legacy_invoke:
            result = await self.runtime.run_prompt(
                session_id,
                "create a contextual candidate",
                debate_id=debate_id,
                output_path="part-01/context-consensus.md",
                max_debate_rounds=2,
            )
        legacy_invoke.assert_not_awaited()
        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.rounds_completed, 2)
        self.assertEqual(result.revision_id, "r2")
        self.assertEqual(
            self.workspace.read_text("manuscript/part-01/context-consensus.md"),
            result.final_candidate,
        )
        currents = [
            _current_material(payload)
            for payload in self.provider.context_payloads
            if _current_material(payload).get("workflow") == "debate"
            and _current_material(payload)["identity"]["debate_id"] == debate_id
        ]
        phases = {current["phase"] for current in currents}
        self.assertEqual(
            phases,
            {"draft", "audit", "propose_revision", "synthesize", "finalize"},
        )
        self.assertEqual(
            {current["identity"]["agent_id"] for current in currents},
            set(ROSTER),
        )
        self.assertGreaterEqual(
            sum(current["phase"] == "audit" for current in currents),
            len(SPECIALIST_IDS) * 3,
        )
        inspection = self.workspace.inspect_debate(session_id, debate_id)
        self.assertEqual(inspection["status"], "consensus")
        self.assertIs(inspection["terminal"], True)
        self.assertEqual(inspection["result"]["revision_id"], "r2")
        self.assertTrue(
            all(
                binding.definition.context_document_files == ()
                for binding in self.provider.bindings
            )
        )

    async def test_terminal_deadlock_is_durable_and_never_publishes(self) -> None:
        session_id = "context_debate_deadlock"
        debate_id = "terminal_context_deadlock"
        self.ready(session_id)
        self.provider.conflicts.add((debate_id, "r0", PLOT_ARCHITECT))

        result = await self.runtime.run_prompt(
            session_id,
            "create a candidate that remains disputed",
            debate_id=debate_id,
            output_path="part-01/context-deadlock.md",
            max_debate_rounds=0,
        )

        self.assertIs(result.status, DebateStatus.DEADLOCK)
        self.assertEqual(result.rounds_completed, 0)
        self.assertEqual(result.revision_id, "r0")
        self.assertIsNone(result.final_candidate)
        self.assertIsNotNone(result.latest_candidate)
        self.assertIn("User ruling required", result.action_required)
        self.assertFalse(
            self.workspace.path("manuscript/part-01/context-deadlock.md").exists()
        )
        inspection = self.workspace.inspect_debate(session_id, debate_id)
        self.assertEqual(inspection["status"], "deadlock")
        self.assertIs(inspection["terminal"], True)
        self.assertEqual(
            inspection["result"]["latest_candidate"], result.latest_candidate
        )
        debate_currents = [
            current
            for current in self.provider.current_by_operation.values()
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
        ]
        self.assertNotIn("finalize", {current["phase"] for current in debate_currents})
        self.assertIn(
            "context_checkpoint_advanced",
            [
                record.get("outcome")
                for record in self.context_debug_records(session_id)
            ],
        )

    async def test_core_modifications_and_create_only_manuscript_revision(
        self,
    ) -> None:
        scenarios = (
            (
                "outline_change",
                "global_outline.md",
                PLOT_ARCHITECT,
                "updated global outline from contextual debate",
            ),
            (
                "character_change",
                "character_sheets.md",
                CHARACTER_CUSTODIAN,
                "updated character sheet from contextual debate",
            ),
            (
                "rules_change",
                "narrative_rules.md",
                EXECUTION_SPECIALIST,
                "updated narrative rules from contextual debate",
            ),
        )
        for debate_id, filename, responsible_agent, replacement in scenarios:
            session_id = f"session_{debate_id}"
            self.ready(session_id)
            self.provider.final_updates[debate_id] = {filename: replacement}
            result = await self.runtime.run_prompt(
                session_id,
                f"modify {filename} while preserving its constraints",
                debate_id=debate_id,
                output_path=f"part-01/{debate_id}.md",
            )
            with self.subTest(debate_id=debate_id):
                self.assertIs(result.status, DebateStatus.CONSENSUS)
                self.assertEqual(
                    self.workspace.read_text(f"core_documents/{filename}"),
                    replacement,
                )
                self.assertTrue(
                    self.workspace.path(
                        f"manuscript/part-01/{debate_id}.md"
                    ).exists()
                )
                relevant_payloads = [
                    payload
                    for payload in self.provider.context_payloads
                    if (
                        (current := _current_material(payload))["workflow"]
                        == "debate"
                        and current["identity"]["debate_id"] == debate_id
                        and current["identity"]["agent_id"] == responsible_agent
                    )
                ]
                self.assertTrue(relevant_payloads)
                source_id = self.source_id(filename)
                source_entries = [
                    entry
                    for payload in relevant_payloads
                    for entry in payload["available_sources"]
                    if entry["source_id"] == source_id
                ]
                self.assertTrue(source_entries)
                self.assertTrue(
                    all(
                        entry["semantic_channel"] == "hard_constraint"
                        and entry["priority"] == "p2_working"
                        for entry in source_entries
                    )
                )

        repository = WritingArtifactRepository(self.workspace.root)
        old_content = "OLD_MANUSCRIPT_CONTEXT_NEEDLE\nunchanged original\n"
        repository.commit_consensus(
            output_path="part-01/original.md",
            candidate=old_content,
            core_updates={},
            session_id="fixture_revision",
            debate_id="fixture_revision",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )
        session_id = "session_manuscript_revision"
        debate_id = "create_only_revision"
        self.ready(session_id)
        revised_content = "REVISED_MANUSCRIPT_FROM_CONTEXT"
        self.provider.candidates[debate_id] = revised_content
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), "OLD_MANUSCRIPT_CONTEXT_NEEDLE")
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
            and current["phase"] == "draft"
            else None
        )
        result = await self.runtime.run_prompt(
            session_id,
            "revise the existing manuscript as a new artifact",
            debate_id=debate_id,
            output_path="part-01/revised.md",
        )
        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(
            self.workspace.read_text("manuscript/part-01/original.md"), old_content
        )
        self.assertEqual(
            self.workspace.read_text("manuscript/part-01/revised.md"),
            revised_content,
        )
        self.assertTrue(self.provider.read_frames)
        self.assertIn(
            "OLD_MANUSCRIPT_CONTEXT_NEEDLE",
            json.dumps(self.provider.read_frames[-1], ensure_ascii=False),
        )

    async def test_references_are_ordered_per_agent_and_search_independent(
        self,
    ) -> None:
        session_id = "context_references"
        debate_id = "context_reference_debate"
        self.ready(session_id)
        outline = "outline first\noutline second needle\n"
        characters = "character first\ncharacter selected line\ncharacter third\n"
        self.workspace.path("core_documents/global_outline.md").write_text(
            outline, encoding="utf-8"
        )
        self.workspace.path("core_documents/character_sheets.md").write_text(
            characters, encoding="utf-8"
        )
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), "outline second needle")
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
            and current["phase"] == "draft"
            else None
        )
        prompt = (
            "Use ${core_documents/global_outline.md} then "
            "${core_documents/character_sheets.md}#L2-L2 exactly."
        )
        result = await self.runtime.run_prompt(
            session_id,
            prompt,
            debate_id=debate_id,
            output_path="part-01/context-references.md",
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertIsNotNone(result.reference_operation_id)
        debate_initial_payloads = [
            payload
            for payload in self.provider.context_payloads
            if (
                (current := _current_material(payload))["workflow"] == "debate"
                and current["identity"]["debate_id"] == debate_id
            )
        ]
        self.assertEqual(
            len(self.provider.reference_payloads), len(debate_initial_payloads)
        )
        self.assertEqual(
            {
                payload["correlation"]["agent_id"]
                for payload in self.provider.reference_payloads
            },
            set(ROSTER),
        )
        expected_references = [
            {"path": "core_documents/global_outline.md", "line_range": None},
            {
                "path": "core_documents/character_sheets.md",
                "line_range": {"start": 2, "end": 2},
            },
        ]
        self.assertTrue(
            all(
                payload["references"] == expected_references
                for payload in self.provider.reference_payloads
            )
        )
        for payload in debate_initial_payloads:
            mandatory = payload["mandatory_references"]
            self.assertEqual([item["ordinal"] for item in mandatory], [1, 2])
            self.assertTrue(all("content" not in item for item in mandatory))
            attached = payload["untrusted_writing_artifact_context"]
            self.assertEqual(
                [item["reference"] for item in attached], expected_references
            )
            self.assertEqual(
                [item["content"] for item in attached],
                [outline, "character selected line\n"],
            )
        self.assertTrue(self.provider.search_frames)
        self.assertTrue(self.provider.read_frames)
        self.assertIn(
            "outline second needle",
            json.dumps(self.provider.read_frames[-1], ensure_ascii=False),
        )

        history = self.runtime.inspect_reference_operation(
            result.reference_operation_id
        )
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(
            [event.operation_state.value for event in history.operation_events],
            ["requested", "captured", "accepted"],
        )
        self.assertEqual(len(history.invocation_events), len(debate_initial_payloads))
        self.assertTrue(
            all(
                events[-1].invocation_state.value == "accepted"
                for events in history.invocation_events
            )
        )
        encoded_history = repr(history)
        self.assertNotIn("outline second needle", encoded_history)
        self.assertNotIn("character selected line", encoded_history)

    async def test_precommit_failure_matrix_never_publishes_or_advances(self) -> None:
        async def run_failed_debate(
            label: str,
            *,
            prompt: str = "exercise contextual failure handling",
        ) -> tuple[str, object]:
            session_id = f"failure_{label}"
            debate_id = f"failure_{label}_debate"
            self.ready(session_id)
            result = await self.runtime.run_prompt(
                session_id,
                prompt,
                debate_id=debate_id,
                output_path=f"part-01/failure-{label}.md",
            )
            self.assertIs(result.status, DebateStatus.FAILED)
            self.assertFalse(
                self.workspace.path(f"manuscript/part-01/failure-{label}.md").exists()
            )
            outcomes = [
                record.get("outcome")
                for record in self.context_debug_records(session_id)
            ]
            self.assertIn("context_checkpoint_not_advanced", outcomes)
            self.assertNotIn("context_checkpoint_advanced", outcomes)
            inspection = self.workspace.inspect_debate(session_id, debate_id)
            self.assertEqual(inspection["status"], "failed")
            self.assertIs(inspection["terminal"], True)
            return session_id, result

        malformed_id = "failure_malformed_debate"
        self.provider.invalid_domain_selector = lambda current: (
            current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == malformed_id
            and current["phase"] == "draft"
        )
        malformed_session, _ = await run_failed_debate("malformed")
        malformed_outcomes = [
            record.get("outcome")
            for record in self.context_debug_records(malformed_session)
        ]
        self.assertIn("context_tainted", malformed_outcomes)
        self.provider.invalid_domain_selector = lambda _current: False

        budget_id = "failure_budget_debate"
        self.workspace.path("core_documents/narrative_rules.md").write_text(
            "BUDGET_SEARCH_NEEDLE\n",
            encoding="utf-8",
        )
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), "BUDGET_SEARCH_NEEDLE")
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == budget_id
            and current["phase"] == "draft"
            else None
        )
        self.provider.search_only_selector = lambda current: (
            current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == budget_id
            and current["phase"] == "draft"
        )
        budget_session, _ = await run_failed_debate("budget")
        budget_outcomes = [
            record.get("outcome")
            for record in self.context_debug_records(budget_session)
        ]
        self.assertIn("context_budget_exhausted", budget_outcomes)
        self.provider.search_only_selector = lambda _current: False

        exhaustion_id = "failure_exhaustion_debate"
        self.workspace.path("core_documents/narrative_rules.md").write_text(
            "EXHAUSTION_SEARCH_NEEDLE\n",
            encoding="utf-8",
        )
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), "EXHAUSTION_SEARCH_NEEDLE")
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == exhaustion_id
            and current["phase"] == "draft"
            else None
        )
        self.provider.exhaust_selector = lambda current: (
            current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == exhaustion_id
            and current["phase"] == "draft"
        )
        exhaustion_session, _ = await run_failed_debate("exhaustion")
        exhaustion_outcomes = [
            record.get("outcome")
            for record in self.context_debug_records(exhaustion_session)
        ]
        self.assertEqual(exhaustion_outcomes.count("context_search_completed"), 2)
        self.assertEqual(exhaustion_outcomes.count("context_read_completed"), 2)
        self.assertIn("context_tainted", exhaustion_outcomes)
        self.provider.exhaust_selector = lambda _current: False

        stale_id = "failure_stale_debate"
        stale_path = self.workspace.path("core_documents/narrative_rules.md")
        stale_path.write_text("STALE_SOURCE_NEEDLE\n", encoding="utf-8")
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), "STALE_SOURCE_NEEDLE")
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == stale_id
            and current["phase"] == "draft"
            else None
        )
        self.provider.mutate_before_answer = lambda _current: stale_path.write_text(
            "source changed after authorized read\n", encoding="utf-8"
        )
        stale_session, _ = await run_failed_debate("stale")
        stale_outcomes = [
            record.get("outcome")
            for record in self.context_debug_records(stale_session)
        ]
        self.assertIn("context_invalidated", stale_outcomes)

        self.provider.search_selector = lambda _current: None
        self.provider.reference_failure = True
        reference_session, _ = await run_failed_debate(
            "reference",
            prompt="Use ${core_documents/global_outline.md} exactly.",
        )
        reference_outcomes = [
            record.get("outcome")
            for record in self.context_debug_records(reference_session)
        ]
        self.assertIn("context_checkpoint_not_advanced", reference_outcomes)
        self.provider.reference_failure = False

        retrieval_session = "failure_citation"
        self.ready(retrieval_session)
        selected = self.conversation(retrieval_session, "Citation selected")
        source = self.conversation(retrieval_session, "Citation source")
        self.append_turn(
            retrieval_session,
            source,
            "citation_source_turn",
            "UNAUTHORIZED_CITATION_NEEDLE",
            "citation source reply",
        )
        memory = ConversationMemoryService(
            ConversationSearchScope(retrieval_session),
            self.services.catalog,
            self.services.event_store,
        )
        self.provider.search_selector = lambda current: (
            (("conversation",), "UNAUTHORIZED_CITATION_NEEDLE")
            if current.get("workflow") == "conversation_retrieval"
            else None
        )
        self.provider.invalid_citation_selector = lambda current: (
            current.get("workflow") == "conversation_retrieval"
        )
        with self.assertRaises(ConversationRetrievalSemanticError):
            await self.runtime.converse_with_retrieval(
                retrieval_session,
                selected,
                PLOT_ARCHITECT,
                "produce an unauthorized citation",
                memory_service=memory,
            )
        retrieval_events = self.services.event_store.read(
            retrieval_session, selected
        )
        self.assertEqual(
            tuple(event.kind for event in retrieval_events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        citation_outcomes = [
            record.get("outcome")
            for record in self.context_debug_records(retrieval_session)
        ]
        self.assertIn("context_checkpoint_not_advanced", citation_outcomes)
        self.assertNotIn("context_checkpoint_advanced", citation_outcomes)
