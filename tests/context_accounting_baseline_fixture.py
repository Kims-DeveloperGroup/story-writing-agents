"""Deterministic real-runtime fixture for the provider-context baseline."""

from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from unittest import TestCase, mock

from story_writing_agents import (
    AgentBackend,
    ConversationEventAppend,
    ConversationEventKind,
    DebateStatus,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    PLOT_ARCHITECT,
    Phase,
    StoryRuntime,
)
from story_writing_agents.agent_context import (
    ContextComponentBytes,
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    canonical_provider_json,
)
from story_writing_agents.conversation_search import (
    ConversationMemoryService,
    ConversationSearchScope,
)
from story_writing_agents.models import AgentDefinition
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderInvocation,
    ProviderResult,
)

from tests.helpers import all_manuscripts, core_snapshot, initialized_workspace


BASELINE_SCHEMA_VERSION = 1
BASELINE_EXPECTED_PHYSICAL_CALLS = 56
BASELINE_GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "agent_context"
    / "provider_context_baseline_v1.json"
)

_PRIVATE_SENTINEL = "BASELINE_PRIVATE_CONTENT"
_FIXED_TIME = datetime(2026, 8, 28, 6, 7, 8, 901234, tzinfo=UTC)
_CORE_INPUTS = {
    "global_outline.md": (
        f"{_PRIVATE_SENTINEL} global outline\n도시의 비밀 🙂\n"
    ),
    "part_outlines.md": f"{_PRIVATE_SENTINEL} part outline\nPart two\n",
    "character_sheets.md": (
        f"{_PRIVATE_SENTINEL} character one\n범위 인물 🙂\ncharacter three\n"
    ),
    "narrative_rules.md": f"{_PRIVATE_SENTINEL} narrative rules\n규칙 둘\n",
}
_ACTION_KEYS = (
    "provider_calls",
    "reference_read_batches",
    "ordinary_conversation_calls",
    "retrieval_search_actions",
    "retrieval_transcript_read_actions",
    "retrieval_answer_actions",
    "debate_draft_actions",
    "debate_audit_actions",
    "debate_proposal_actions",
    "debate_synthesis_actions",
    "debate_finalize_actions",
)
_BYTE_KEYS = (
    "binding_instruction_utf8_bytes",
    "schema_utf8_bytes",
    "payload_utf8_bytes",
    "raw_mandatory_reference_utf8_bytes",
    "gross_model_input_utf8_bytes",
    "non_reference_model_input_utf8_bytes",
    "actual_transport_utf8_bytes",
)
_CONTROLLED_ELAPSED_NS = {
    ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ: 2_000_000,
    ProviderCallKind.CONVERSATION: 3_000_000,
    ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL: 4_000_000,
    ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION: 4_000_000,
    ProviderCallKind.DEBATE: 5_000_000,
    ProviderCallKind.UNCLASSIFIED: 1_000_000,
}


class _FixedDateTime(datetime):
    """Return one stable timestamp wherever the runtime asks for now."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        value = _FIXED_TIME
        return value if tz is None else value.astimezone(tz)


class _UuidSequence:
    """Supply deterministic valid UUIDs without retaining global state."""

    def __init__(self) -> None:
        self._next = 1

    def __call__(self) -> uuid.UUID:
        value = uuid.UUID(int=self._next)
        self._next += 1
        return value


@contextmanager
def deterministic_runtime_environment() -> Iterator[None]:
    """Fix clocks and identities and reject provider/network subprocess use."""

    identifiers = _UuidSequence()

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("baseline attempted subprocess or network provider work")

    with ExitStack() as stack:
        stack.enter_context(mock.patch("uuid.uuid4", new=identifiers))
        for target in (
            "story_writing_agents.workspace.core.uuid4",
            "story_writing_agents.writing_artifacts.repository.uuid4",
            "story_writing_agents.writing_artifacts._storage.uuid4",
        ):
            stack.enter_context(mock.patch(target, new=identifiers))
        for target in (
            "story_writing_agents.orchestrator.datetime",
            "story_writing_agents.workspace.core.datetime",
            "story_writing_agents.debug_log.datetime",
            "story_writing_agents.writing_artifacts.repository.datetime",
        ):
            stack.enter_context(mock.patch(target, new=_FixedDateTime))
        stack.enter_context(
            mock.patch("asyncio.create_subprocess_exec", side_effect=forbidden)
        )
        stack.enter_context(mock.patch.object(subprocess, "run", side_effect=forbidden))
        stack.enter_context(
            mock.patch("socket.create_connection", side_effect=forbidden)
        )
        yield


@dataclass(frozen=True, slots=True)
class _RouterPolicy:
    """One scenario's provider response decisions."""

    scenario_id: str
    retrieval_actions: tuple[str, ...] = ()
    conflicting_revisions: frozenset[str] = frozenset()
    core_updates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _InvocationTrace:
    """Ephemeral content-bearing trace used only for quality assertions."""

    invocation: ProviderInvocation
    response_action_kind: str | None
    controlled_elapsed_ns: int


class _DeterministicCodexAdapter:
    """Retain real binding behavior while replacing only provider execution."""

    vendor = "codex"

    def __init__(self, delegate: object, policy: _RouterPolicy) -> None:
        self._delegate = delegate
        self.policy = policy
        self.traces: list[_InvocationTrace] = []

    def bind(self, definition: AgentDefinition) -> ProviderBinding:
        return self._delegate.bind(definition)  # type: ignore[no-any-return,union-attr]

    def release(self, binding: ProviderBinding) -> None:
        self._delegate.release(binding)  # type: ignore[union-attr]

    def close(self) -> None:
        self._delegate.close()  # type: ignore[union-attr]

    def executable_available(self) -> bool:
        return True

    async def invoke(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        if binding.vendor != self.vendor or invocation.payload_json is None:
            raise AssertionError("baseline received an invalid provider invocation")
        response, action_kind = self._response(invocation)
        elapsed = _CONTROLLED_ELAPSED_NS[invocation.call_kind]
        self.traces.append(_InvocationTrace(invocation, action_kind, elapsed))
        thread_id = invocation.thread_id
        if thread_id is None:
            thread_id = (
                f"thread-{self.policy.scenario_id}-"
                f"{binding.definition.agent_id}"
            )
        return ProviderResult(
            thread_id=thread_id,
            response_text=canonical_provider_json(response).decode("utf-8"),
            usage=None,
            actual_transport_utf8_bytes=len(invocation.payload_json),
        )

    def _response(
        self,
        invocation: ProviderInvocation,
    ) -> tuple[dict[str, object], str | None]:
        payload = invocation.payload
        if invocation.call_kind is ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ:
            return (
                {
                    "action": {
                        "kind": "read_writing_artifacts",
                        "correlation": payload["correlation"],
                        "references": payload["references"],
                    }
                },
                "read_writing_artifacts",
            )
        if invocation.call_kind in {
            ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
            ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION,
        }:
            retrieval = payload["retrieval"]
            assert isinstance(retrieval, Mapping)
            action_index = retrieval["action_index"]
            assert type(action_index) is int
            kind = self.policy.retrieval_actions[action_index - 1]
            action: dict[str, object] = {
                "kind": kind,
                "session_id": payload["session_id"],
                "conversation_id": payload["conversation_id"],
                "turn_id": payload["turn_id"],
                "action_index": action_index,
            }
            if kind == "search_conversations":
                action["query"] = "Needle source"
            elif kind == "read_transcript":
                action.update(
                    {
                        "citation_id": "s1h1",
                        "start_sequence": 1,
                        "end_sequence": 2,
                    }
                )
            elif kind == "answer":
                action.update(
                    {
                        "content": f"{_PRIVATE_SENTINEL} retrieval answer 🙂",
                        "citation_ids": (
                            ["r2e1", "r2e2"]
                            if "read_transcript" in self.policy.retrieval_actions
                            else []
                        ),
                    }
                )
            else:
                raise AssertionError(f"unknown retrieval fixture action: {kind}")
            return {"action": action}, kind
        if invocation.call_kind is ProviderCallKind.CONVERSATION:
            return (
                {
                    "session_id": payload["session_id"],
                    "conversation_id": payload["conversation_id"],
                    "turn_id": payload["turn_id"],
                    "content": f"{_PRIVATE_SENTINEL} conversation reply 한🙂",
                },
                None,
            )
        if invocation.call_kind is not ProviderCallKind.DEBATE:
            raise AssertionError("baseline provider call was not classified")
        phase = payload["phase"]
        revision = payload["revision_id"]
        if phase == Phase.AUDIT.value:
            conflict = (
                payload["agent_id"] == PLOT_ARCHITECT
                and revision in self.policy.conflicting_revisions
            )
            return (
                {
                    "revision_id": revision,
                    "verdict": "conflict" if conflict else "approve",
                    "content": (
                        f"{_PRIVATE_SENTINEL} conflict {revision}"
                        if conflict
                        else ""
                    ),
                },
                None,
            )
        if phase == Phase.FINALIZE.value:
            return (
                {
                    "revision_id": revision,
                    "core_updates": [
                        {"filename": filename, "content": content}
                        for filename, content in self.policy.core_updates
                    ],
                },
                None,
            )
        return (
            {
                "revision_id": revision,
                "content": (
                    f"{_PRIVATE_SENTINEL} {self.policy.scenario_id} "
                    f"{phase} {revision} 한🙂"
                ),
            },
            None,
        )


class _Harness:
    """One isolated real backend/runtime and its exact accounting records."""

    def __init__(
        self,
        test_case: TestCase,
        policy: _RouterPolicy,
        *,
        session_id: str,
    ) -> None:
        self.test_case = test_case
        self.policy = policy
        self.session_id = session_id
        self.workspace = initialized_workspace(test_case)
        for filename, content in _CORE_INPUTS.items():
            self.workspace.path(f"core_documents/{filename}").write_text(
                content,
                encoding="utf-8",
            )
        self.backend = AgentBackend(
            self.workspace,
            executable="baseline-provider-must-not-run",
        )
        test_case.addCleanup(self.backend.close)
        self.adapter = _DeterministicCodexAdapter(
            self.backend._codex_adapter,
            policy,
        )
        self.backend._adapters["codex"] = self.adapter
        self.records: list[ProviderCallAccounting] = []
        self.backend._context_accounting_sink = self.records.append
        self.backend.create_session_with_access(session_id)
        self.catalog = FilesystemConversationCatalog(self.workspace)
        self.store = FilesystemConversationEventStore(
            self.workspace,
            catalog=self.catalog,
        )
        self.runtime = StoryRuntime(
            self.workspace,
            self.backend,
            conversation_event_store=self.store,
        )

    def scenario_report(self, scenario_id: str, start: int) -> dict[str, object]:
        records = self.records[start:]
        traces = self.adapter.traces[start:]
        self.test_case.assertEqual(len(records), len(traces))
        calls = [
            _call_report(index, record, trace)
            for index, (record, trace) in enumerate(
                zip(records, traces, strict=True),
                1,
            )
        ]
        return {
            "scenario_id": scenario_id,
            "physical_provider_calls": len(calls),
            "action_calls": _action_counts(traces),
            "calls": calls,
            "byte_totals": _byte_totals(calls),
            "component_totals": _component_totals(calls),
            "controlled_provider_elapsed_ns": sum(
                trace.controlled_elapsed_ns for trace in traces
            ),
        }


def _component_report(
    component: ContextComponentBytes,
    *,
    source: str,
) -> dict[str, object]:
    return {
        "source": source,
        "kind": component.kind.value,
        "identity": component.identity,
        "serialized_utf8_bytes": component.serialized_utf8_bytes,
        "raw_mandatory_reference_utf8_bytes": (
            component.raw_mandatory_reference_utf8_bytes
        ),
    }


def _call_report(
    ordinal: int,
    record: ProviderCallAccounting,
    trace: _InvocationTrace,
) -> dict[str, object]:
    if record.state is not ProviderCallState.SUBMITTED:
        raise AssertionError("baseline provider call was not submitted")
    if record.usage is not None:
        raise AssertionError("baseline provider usage must remain unavailable")
    if record.actual_transport_utf8_bytes is None:
        raise AssertionError("baseline transport bytes are unavailable")
    return {
        "ordinal": ordinal,
        "kind": record.payload.call_kind.value,
        "state": record.state.value,
        "binding_instruction_utf8_bytes": record.binding_instruction_utf8_bytes,
        "schema_utf8_bytes": record.payload.schema_utf8_bytes,
        "payload_utf8_bytes": record.payload.payload_utf8_bytes,
        "raw_mandatory_reference_utf8_bytes": (
            record.payload.raw_mandatory_reference_utf8_bytes
        ),
        "gross_model_input_utf8_bytes": record.gross_model_input_utf8_bytes,
        "non_reference_model_input_utf8_bytes": (
            record.non_reference_model_input_utf8_bytes
        ),
        "actual_transport_utf8_bytes": record.actual_transport_utf8_bytes,
        "controlled_provider_elapsed_ns": trace.controlled_elapsed_ns,
        "usage": None,
        "binding_components": [
            _component_report(component, source="binding")
            for component in record.binding_components
        ],
        "payload_components": [
            _component_report(component, source="payload")
            for component in record.payload.components
        ],
    }


def _action_counts(traces: list[_InvocationTrace]) -> dict[str, int]:
    counts = {key: 0 for key in _ACTION_KEYS}
    for trace in traces:
        counts["provider_calls"] += 1
        kind = trace.invocation.call_kind
        if kind is ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ:
            counts["reference_read_batches"] += 1
        elif kind is ProviderCallKind.CONVERSATION:
            counts["ordinary_conversation_calls"] += 1
        elif kind in {
            ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
            ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION,
        }:
            key = {
                "search_conversations": "retrieval_search_actions",
                "read_transcript": "retrieval_transcript_read_actions",
                "answer": "retrieval_answer_actions",
            }[trace.response_action_kind]
            counts[key] += 1
        elif kind is ProviderCallKind.DEBATE:
            phase = trace.invocation.payload["phase"]
            key = {
                Phase.DRAFT.value: "debate_draft_actions",
                Phase.AUDIT.value: "debate_audit_actions",
                Phase.PROPOSE_REVISION.value: "debate_proposal_actions",
                Phase.SYNTHESIZE.value: "debate_synthesis_actions",
                Phase.FINALIZE.value: "debate_finalize_actions",
            }[phase]
            counts[key] += 1
        else:
            raise AssertionError("baseline contains an unclassified call")
    return counts


def _byte_totals(calls: list[dict[str, object]]) -> dict[str, int]:
    return {
        key: sum(int(call[key]) for call in calls)
        for key in _BYTE_KEYS
    }


def _component_totals(calls: list[dict[str, object]]) -> list[dict[str, object]]:
    totals: dict[tuple[str, str, str | None], list[int]] = {}
    for call in calls:
        components = [
            *call["binding_components"],  # type: ignore[misc]
            *call["payload_components"],  # type: ignore[misc]
        ]
        for component in components:
            assert isinstance(component, dict)
            key = (
                str(component["source"]),
                str(component["kind"]),
                component["identity"],  # type: ignore[arg-type]
            )
            values = totals.setdefault(key, [0, 0])
            values[0] += int(component["serialized_utf8_bytes"])
            values[1] += int(component["raw_mandatory_reference_utf8_bytes"])
    order = {kind.value: index for index, kind in enumerate(ContextComponentKind)}
    return [
        {
            "source": source,
            "kind": kind,
            "identity": identity,
            "serialized_utf8_bytes": totals[(source, kind, identity)][0],
            "raw_mandatory_reference_utf8_bytes": totals[
                (source, kind, identity)
            ][1],
        }
        for source, kind, identity in sorted(
            totals,
            key=lambda item: (
                0 if item[0] == "binding" else 1,
                order[item[1]],
                "" if item[2] is None else item[2],
            ),
        )
    ]


def _expected_actions(**changes: int) -> dict[str, int]:
    values = {key: 0 for key in _ACTION_KEYS}
    values.update(changes)
    return values


class _BaselineBuilder:
    """Run every stable scenario and retain only content-free measurements."""

    def __init__(self, test_case: TestCase) -> None:
        self.test_case = test_case
        self.scenarios: list[dict[str, object]] = []

    async def build(self) -> dict[str, object]:
        await self._conversation_scenarios()
        await self._retrieval_scenarios()
        await self._reference_scenarios()
        await self._debate_scenarios()
        report = self._report()
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.test_case.assertNotIn(_PRIVATE_SENTINEL, encoded)
        self.test_case.assertNotIn("도시의 비밀", encoded)
        self.test_case.assertNotIn("범위 인물", encoded)
        return report

    def _harness(
        self,
        scenario_id: str,
        *,
        retrieval_actions: tuple[str, ...] = (),
        conflicting_revisions: frozenset[str] = frozenset(),
        core_updates: tuple[tuple[str, str], ...] = (),
    ) -> _Harness:
        return _Harness(
            self.test_case,
            _RouterPolicy(
                scenario_id,
                retrieval_actions,
                conflicting_revisions,
                core_updates,
            ),
            session_id=f"session_{scenario_id}",
        )

    def _append(
        self,
        harness: _Harness,
        scenario_id: str,
        start: int,
        *,
        calls: int,
        actions: dict[str, int],
    ) -> None:
        scenario = harness.scenario_report(scenario_id, start)
        self.test_case.assertEqual(scenario["physical_provider_calls"], calls)
        self.test_case.assertEqual(scenario["action_calls"], actions)
        self.scenarios.append(scenario)

    async def _conversation_scenarios(self) -> None:
        harness = self._harness("conversation_pair")
        selected = harness.catalog.create(harness.session_id, "Selected conversation")
        with mock.patch.object(
            StoryRuntime,
            "_new_conversation_turn_id",
            return_value="turn_fresh",
        ):
            fresh = await harness.runtime.converse(
                harness.session_id,
                selected.conversation_id,
                PLOT_ARCHITECT,
                f"{_PRIVATE_SENTINEL} fresh question 한🙂",
            )
        self.test_case.assertEqual(fresh.context.total_complete_turns, 0)
        self.test_case.assertEqual(
            len(harness.store.read(harness.session_id, selected.conversation_id)),
            2,
        )
        self._append(
            harness,
            "fresh_ordinary_conversation",
            0,
            calls=1,
            actions=_expected_actions(
                provider_calls=1,
                ordinary_conversation_calls=1,
            ),
        )

        start = len(harness.records)
        with mock.patch.object(
            StoryRuntime,
            "_new_conversation_turn_id",
            return_value="turn_continued",
        ):
            continued = await harness.runtime.converse(
                harness.session_id,
                selected.conversation_id,
                PLOT_ARCHITECT,
                f"{_PRIVATE_SENTINEL} continued question",
            )
        self.test_case.assertEqual(continued.context.total_complete_turns, 1)
        self.test_case.assertEqual(len(continued.context.events), 2)
        self.test_case.assertEqual(
            tuple(event.turn_id for event in continued.context.events),
            ("turn_fresh", "turn_fresh"),
        )
        self.test_case.assertEqual(
            len(harness.store.read(harness.session_id, selected.conversation_id)),
            4,
        )
        self._append(
            harness,
            "continued_ordinary_conversation",
            start,
            calls=1,
            actions=_expected_actions(
                provider_calls=1,
                ordinary_conversation_calls=1,
            ),
        )

    async def _retrieval_scenarios(self) -> None:
        direct = self._harness(
            "retrieval_direct",
            retrieval_actions=("answer",),
        )
        selected = direct.catalog.create(direct.session_id, "Selected direct")
        memory = ConversationMemoryService(
            ConversationSearchScope(direct.session_id),
            direct.catalog,
            direct.store,
        )
        with (
            mock.patch.object(memory, "search", wraps=memory.search) as search,
            mock.patch.object(
                memory,
                "read_transcript",
                wraps=memory.read_transcript,
            ) as read,
            mock.patch.object(
                StoryRuntime,
                "_new_conversation_turn_id",
                return_value="turn_retrieval_direct",
            ),
        ):
            result = await direct.runtime.converse_with_retrieval(
                direct.session_id,
                selected.conversation_id,
                PLOT_ARCHITECT,
                f"{_PRIVATE_SENTINEL} direct retrieval question",
                memory_service=memory,
            )
        search.assert_not_called()
        read.assert_not_called()
        self.test_case.assertEqual(result.sources, ())
        self.test_case.assertEqual(
            tuple(
                event.kind
                for event in direct.store.read(
                    direct.session_id,
                    selected.conversation_id,
                )
            ),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self._append(
            direct,
            "retrieval_direct_answer",
            0,
            calls=1,
            actions=_expected_actions(
                provider_calls=1,
                retrieval_answer_actions=1,
            ),
        )

        searched = self._harness(
            "retrieval_search_read",
            retrieval_actions=(
                "search_conversations",
                "read_transcript",
                "answer",
            ),
        )
        selected = searched.catalog.create(searched.session_id, "Selected search")
        source = searched.catalog.create(searched.session_id, "Needle source")
        for sender, recipient, kind, content in (
            (
                "user",
                PLOT_ARCHITECT,
                ConversationEventKind.USER_MESSAGE,
                f"{_PRIVATE_SENTINEL} source question",
            ),
            (
                PLOT_ARCHITECT,
                "user",
                ConversationEventKind.AGENT_REPLY,
                f"{_PRIVATE_SENTINEL} source answer 한🙂",
            ),
        ):
            searched.store.append(
                ConversationEventAppend(
                    session_id=searched.session_id,
                    conversation_id=source.conversation_id,
                    turn_id="turn_retrieval_source",
                    sender=sender,
                    recipient=recipient,
                    kind=kind,
                    content=content,
                )
            )
        memory = ConversationMemoryService(
            ConversationSearchScope(searched.session_id),
            searched.catalog,
            searched.store,
        )
        with (
            mock.patch.object(memory, "search", wraps=memory.search) as search,
            mock.patch.object(
                memory,
                "read_transcript",
                wraps=memory.read_transcript,
            ) as read,
            mock.patch.object(
                StoryRuntime,
                "_new_conversation_turn_id",
                return_value="turn_retrieval_search",
            ),
        ):
            result = await searched.runtime.converse_with_retrieval(
                searched.session_id,
                selected.conversation_id,
                PLOT_ARCHITECT,
                f"{_PRIVATE_SENTINEL} search retrieval question",
                memory_service=memory,
            )
        search.assert_called_once()
        read.assert_called_once()
        self.test_case.assertEqual(
            tuple(source.citation.sequence for source in result.sources),
            (1, 2),
        )
        self.test_case.assertEqual(
            tuple(
                trace.response_action_kind for trace in searched.adapter.traces
            ),
            ("search_conversations", "read_transcript", "answer"),
        )
        self._append(
            searched,
            "retrieval_search_read_answer",
            0,
            calls=3,
            actions=_expected_actions(
                provider_calls=3,
                retrieval_search_actions=1,
                retrieval_transcript_read_actions=1,
                retrieval_answer_actions=1,
            ),
        )

    async def _reference_scenarios(self) -> None:
        cases = (
            (
                "whole_file_reference",
                "Use ${core_documents/global_outline.md}.",
                (
                    ("core_documents/global_outline.md", None),
                ),
                len(_CORE_INPUTS["global_outline.md"].encode("utf-8")),
            ),
            (
                "ranged_file_reference",
                "Use ${core_documents/character_sheets.md}#L2-L2.",
                (
                    ("core_documents/character_sheets.md", (2, 2)),
                ),
                len("범위 인물 🙂\n".encode("utf-8")),
            ),
            (
                "multiple_ordered_references",
                (
                    "Use ${core_documents/global_outline.md} then "
                    "${manuscript/part-01/reference-source.md}#L2-L2."
                ),
                (
                    ("core_documents/global_outline.md", None),
                    ("manuscript/part-01/reference-source.md", (2, 2)),
                ),
                len(_CORE_INPUTS["global_outline.md"].encode("utf-8"))
                + len("선택 원고 🙂\n".encode("utf-8")),
            ),
        )
        for scenario_id, text, expected_references, raw_bytes in cases:
            harness = self._harness(scenario_id)
            manuscript = harness.workspace.path(
                "manuscript/part-01/reference-source.md"
            )
            manuscript.parent.mkdir(parents=True, exist_ok=True)
            manuscript.write_text(
                f"{_PRIVATE_SENTINEL} omitted\n선택 원고 🙂\nomitted\n",
                encoding="utf-8",
            )
            selected = harness.catalog.create(
                harness.session_id,
                f"Selected {scenario_id}",
            )
            with mock.patch.object(
                StoryRuntime,
                "_new_conversation_turn_id",
                return_value=f"turn_{scenario_id}",
            ):
                result = await harness.runtime.converse(
                    harness.session_id,
                    selected.conversation_id,
                    PLOT_ARCHITECT,
                    text,
                )
            self.test_case.assertIsNotNone(result.reference_operation_id)
            history = harness.runtime.inspect_reference_operation(
                result.reference_operation_id  # type: ignore[arg-type]
            )
            self.test_case.assertIsNotNone(history)
            assert history is not None
            self.test_case.assertEqual(
                history.operation_events[-1].operation_state.value,
                "accepted",
            )
            self.test_case.assertEqual(
                tuple(trace.invocation.call_kind for trace in harness.adapter.traces),
                (
                    ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
                    ProviderCallKind.CONVERSATION,
                ),
            )
            substantive = harness.adapter.traces[1].invocation.payload[
                "untrusted_writing_artifact_context"
            ]
            self.test_case.assertIsInstance(substantive, list)
            projected = []
            for item in substantive:  # type: ignore[union-attr]
                line_range = item["reference"]["line_range"]
                projected.append(
                    (
                        item["reference"]["path"],
                        (
                            None
                            if line_range is None
                            else (line_range["start"], line_range["end"])
                        ),
                    )
                )
            self.test_case.assertEqual(tuple(projected), expected_references)
            self.test_case.assertEqual(
                harness.records[0].payload.raw_mandatory_reference_utf8_bytes,
                0,
            )
            self.test_case.assertEqual(
                harness.records[1].payload.raw_mandatory_reference_utf8_bytes,
                raw_bytes,
            )
            self._append(
                harness,
                scenario_id,
                0,
                calls=2,
                actions=_expected_actions(
                    provider_calls=2,
                    reference_read_batches=1,
                    ordinary_conversation_calls=1,
                ),
            )

    async def _debate_scenarios(self) -> None:
        await self._run_debate(
            "straight_consensus_debate",
            calls=5,
            status=DebateStatus.CONSENSUS,
            expected_revision="r0",
        )
        await self._run_debate(
            "conflict_revision_consensus_debate",
            calls=10,
            status=DebateStatus.CONSENSUS,
            expected_revision="r1",
            conflicting_revisions=frozenset({"r0"}),
        )
        await self._run_debate(
            "terminal_deadlock_debate",
            calls=9,
            status=DebateStatus.DEADLOCK,
            expected_revision="r1",
            conflicting_revisions=frozenset({"r0", "r1"}),
        )
        await self._run_debate(
            "outline_modification",
            calls=5,
            status=DebateStatus.CONSENSUS,
            expected_revision="r0",
            core_updates=(
                (
                    "global_outline.md",
                    f"{_PRIVATE_SENTINEL} revised outline 한🙂\n",
                ),
            ),
        )
        await self._run_debate(
            "character_sheet_modification",
            calls=5,
            status=DebateStatus.CONSENSUS,
            expected_revision="r0",
            core_updates=(
                (
                    "character_sheets.md",
                    f"{_PRIVATE_SENTINEL} revised character sheet 한🙂\n",
                ),
            ),
        )
        await self._run_debate(
            "narrative_rule_modification",
            calls=5,
            status=DebateStatus.CONSENSUS,
            expected_revision="r0",
            core_updates=(
                (
                    "narrative_rules.md",
                    f"{_PRIVATE_SENTINEL} revised narrative rules 한🙂\n",
                ),
            ),
        )
        await self._run_debate(
            "create_only_manuscript_revision",
            calls=5,
            status=DebateStatus.CONSENSUS,
            expected_revision="r0",
            manuscript_revision=True,
        )

    async def _run_debate(
        self,
        scenario_id: str,
        *,
        calls: int,
        status: DebateStatus,
        expected_revision: str,
        conflicting_revisions: frozenset[str] = frozenset(),
        core_updates: tuple[tuple[str, str], ...] = (),
        manuscript_revision: bool = False,
    ) -> None:
        harness = self._harness(
            scenario_id,
            conflicting_revisions=conflicting_revisions,
            core_updates=core_updates,
        )
        before_core = core_snapshot(harness.workspace)
        output = f"part-01/{scenario_id}.md"
        if manuscript_revision:
            old_relative = "manuscript/part-01/chapter-r0.md"
            old = harness.workspace.path(old_relative)
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text(
                f"{_PRIVATE_SENTINEL} prior manuscript 한🙂\n",
                encoding="utf-8",
            )
            prior = harness.workspace.read_text(old_relative)
            source = f"Revise the prior canonical manuscript:\n{prior}"
        else:
            old_relative = None
            prior = None
            source = f"{_PRIVATE_SENTINEL} request {scenario_id} 한🙂"
        result = await harness.runtime.run_draft(
            harness.session_id,
            source,
            output_path=output,
            debate_id=f"debate_{scenario_id}",
        )
        self.test_case.assertIs(result.status, status)
        self.test_case.assertEqual(result.revision_id, expected_revision)
        phases = tuple(
            trace.invocation.payload["phase"] for trace in harness.adapter.traces
        )
        if calls == 5:
            self.test_case.assertEqual(
                phases,
                ("draft", "audit", "audit", "audit", "finalize"),
            )
        elif calls == 10:
            self.test_case.assertEqual(
                phases,
                (
                    "draft",
                    "audit",
                    "audit",
                    "audit",
                    "propose_revision",
                    "synthesize",
                    "audit",
                    "audit",
                    "audit",
                    "finalize",
                ),
            )
        else:
            self.test_case.assertEqual(
                phases,
                (
                    "draft",
                    "audit",
                    "audit",
                    "audit",
                    "propose_revision",
                    "synthesize",
                    "audit",
                    "audit",
                    "audit",
                ),
            )
        if status is DebateStatus.DEADLOCK:
            self.test_case.assertEqual(core_snapshot(harness.workspace), before_core)
            self.test_case.assertEqual(all_manuscripts(harness.workspace), [])
        else:
            manuscript_path = harness.workspace.path(f"manuscript/{output}")
            self.test_case.assertTrue(manuscript_path.is_file())
            changed = {
                filename
                for filename, content in core_snapshot(harness.workspace).items()
                if content != before_core[filename]
            }
            self.test_case.assertEqual(
                changed,
                {filename for filename, _content in core_updates},
            )
        if manuscript_revision:
            assert old_relative is not None and prior is not None
            self.test_case.assertEqual(
                harness.workspace.read_text(old_relative),
                prior,
            )
            self.test_case.assertNotEqual(
                harness.workspace.read_text(f"manuscript/{output}"),
                prior,
            )
        audit_count = 6 if calls in {9, 10} else 3
        self._append(
            harness,
            scenario_id,
            0,
            calls=calls,
            actions=_expected_actions(
                provider_calls=calls,
                debate_draft_actions=1,
                debate_audit_actions=audit_count,
                debate_proposal_actions=(1 if calls in {9, 10} else 0),
                debate_synthesis_actions=(1 if calls in {9, 10} else 0),
                debate_finalize_actions=(1 if status is DebateStatus.CONSENSUS else 0),
            ),
        )

    def _report(self) -> dict[str, object]:
        self.test_case.assertEqual(len(self.scenarios), 14)
        all_calls = [
            call
            for scenario in self.scenarios
            for call in scenario["calls"]  # type: ignore[union-attr]
        ]
        self.test_case.assertEqual(
            len(all_calls),
            BASELINE_EXPECTED_PHYSICAL_CALLS,
        )
        cumulative_actions = {
            key: sum(
                scenario["action_calls"][key]  # type: ignore[index]
                for scenario in self.scenarios
            )
            for key in _ACTION_KEYS
        }
        self.test_case.assertEqual(
            cumulative_actions,
            _expected_actions(
                provider_calls=56,
                reference_read_batches=3,
                ordinary_conversation_calls=5,
                retrieval_search_actions=1,
                retrieval_transcript_read_actions=1,
                retrieval_answer_actions=2,
                debate_draft_actions=7,
                debate_audit_actions=27,
                debate_proposal_actions=2,
                debate_synthesis_actions=2,
                debate_finalize_actions=6,
            ),
        )
        cumulative_bytes = _byte_totals(all_calls)
        baseline_non_reference = cumulative_bytes[
            "non_reference_model_input_utf8_bytes"
        ]
        return {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "measurement_contract": {
                "encoder": (
                    "compact insertion-ordered strict UTF-8 JSON; "
                    "ensure_ascii=false"
                ),
                "gross_formula": "binding + schema + inner_prompt",
                "non_reference_formula": (
                    "gross - exact raw mandatory-reference source bytes delivered"
                ),
                "transport": (
                    "Codex inner prompt bytes; Antigravity outer NDJSON is "
                    "diagnostic transport only"
                ),
                "provider_usage": (
                    "unavailable in baseline; never estimated; Antigravity "
                    "conversation-cumulative snapshots must not be summed as deltas"
                ),
            },
            "quality_invariants": {
                "provider_free": True,
                "network_free": True,
                "subprocess_free": True,
                "semantic_rag_disabled": True,
                "fixed_ids_timestamps_responses_revisions_paths_schemas": True,
                "content_free_report": True,
                "canonical_manuscript_directory": "manuscript/",
            },
            "scenarios": self.scenarios,
            "cumulative": {
                "scenario_count": len(self.scenarios),
                "physical_provider_calls": len(all_calls),
                "action_calls": cumulative_actions,
                "byte_totals": cumulative_bytes,
                "component_totals": _component_totals(all_calls),
                "controlled_provider_elapsed_ns": sum(
                    int(call["controlled_provider_elapsed_ns"])
                    for call in all_calls
                ),
                "provider_usage": None,
            },
            "later_reduction_ceiling": {
                "basis": "cumulative non-reference model input UTF-8 bytes",
                "ratio_numerator": 1,
                "ratio_denominator": 2,
                "comparison": "candidate_bytes * 2 <= baseline_bytes",
                "baseline_utf8_bytes": baseline_non_reference,
                "maximum_candidate_utf8_bytes": baseline_non_reference // 2,
            },
        }


async def build_provider_context_baseline(test_case: TestCase) -> dict[str, object]:
    """Return the complete deterministic content-free provider baseline."""

    with deterministic_runtime_environment():
        return await _BaselineBuilder(test_case).build()
