"""Deterministic real-runtime fixture for optimized compact-wire accounting."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest import TestCase, mock

from story_writing_agents import (
    AgentBackend,
    ConversationEventAppend,
    ConversationEventKind,
    PLOT_ARCHITECT,
    Phase,
    StoryRuntime,
)
from story_writing_agents.agent_context import (
    ProviderCallAccounting,
    ProviderCallKind,
    canonical_provider_json,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_search import (
    ConversationMemoryService,
    ConversationSearchScope,
)
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderInvocation,
    ProviderResult,
)

from tests.context_accounting_baseline_fixture import (
    BASELINE_GOLDEN_PATH,
    _CONTROLLED_ELAPSED_NS,
    _CORE_INPUTS,
    _InvocationTrace,
    _PRIVATE_SENTINEL,
    _RouterPolicy,
    _BaselineBuilder,
    _action_counts,
    _byte_totals,
    _call_report,
    _component_totals,
    _expected_actions,
    deterministic_runtime_environment,
)
from tests.helpers import initialized_workspace


# Version 1 is the optimized report format, not the provider wire version.
OPTIMIZED_REPORT_SCHEMA_VERSION = 1
OPTIMIZED_COMPACT_WIRE_VERSION = 5
OPTIMIZED_EXPECTED_PHYSICAL_CALLS = 56
OPTIMIZED_GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "agent_context"
    / "provider_context_optimized_v1.json"
)


@dataclass(frozen=True, slots=True)
class _SemanticInvocation:
    """Only the full-domain facts needed by the frozen workload assertions."""

    call_kind: ProviderCallKind
    payload: Mapping[str, object]


def _current_material(payload: Mapping[str, object]) -> dict[str, Any]:
    query = payload.get("q")
    if type(query) is not list or not query or type(query[0]) is not str:
        raise AssertionError("compact initial payload omitted its phase contract")
    phase = query[0]
    current = payload.get("i")
    if type(current) is str:
        return {
            "workflow": (
                "conversation_retrieval"
                if phase == "retrieval"
                else "conversation"
                if phase == "conversation"
                else "debate"
            ),
            "command": current,
            "phase": phase,
        }
    if isinstance(current, Mapping) and type(current.get("input")) is str:
        return {
            "workflow": "conversation_retrieval",
            "command": current["input"],
            "rag_context": current.get("rag"),
            "phase": phase,
        }
    raise AssertionError("compact initial payload omitted current request material")


def _observation(value: object) -> tuple[str, tuple[str, ...]]:
    """Return one compact observation kind and its authorized handles."""

    if type(value) is not list or not value:
        raise AssertionError("compact continuation omitted its observation")
    if value[0] == "s" and len(value) == 3 and type(value[2]) is list:
        handles = tuple(
            item[5]
            for item in value[2]
            if type(item) is list
            and len(item) == 7
            and type(item[5]) is str
        )
        return "search", handles
    if value[0] == "r" and len(value) == 2 and type(value[1]) is list:
        handles = tuple(
            item[4]
            for item in value[1]
            if type(item) is list
            and len(item) == 6
            and type(item[4]) is str
        )
        return "read", handles
    raise AssertionError("compact observation shape changed")


class _DeterministicCompactAdapter:
    """Retain real binding/accounting while replacing only provider execution."""

    vendor = "codex"

    def __init__(self, delegate: object, policy: _RouterPolicy) -> None:
        self._delegate = delegate
        self.policy = policy
        self.traces: list[_InvocationTrace] = []
        self._current_by_thread: dict[str, dict[str, Any]] = {}
        self._retrieval_action_index = 0
        self._audit_count_by_agent: dict[str, int] = {}

    def bind(self, definition: object) -> ProviderBinding:
        return self._delegate.bind(definition)  # type: ignore[no-any-return,union-attr]

    def release(self, binding: ProviderBinding) -> None:
        self._delegate.release(binding)  # type: ignore[union-attr]

    def close(self) -> None:
        self._delegate.close()  # type: ignore[union-attr]

    def executable_available(self) -> bool:
        return True

    @staticmethod
    def _json(value: object) -> str:
        return canonical_provider_json(value).decode("utf-8")

    def _thread_id(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> str:
        return invocation.thread_id or (
            f"thread-{self.policy.scenario_id}-{binding.definition.agent_id}"
        )

    @staticmethod
    def _correlation(payload: Mapping[str, object]) -> str:
        value = payload.get("c")
        if type(value) is not str or len(value) != 43:
            raise AssertionError("compact correlation token shape changed")
        return value

    @classmethod
    def _wire_action(
        cls,
        payload: Mapping[str, object],
        code: str,
        data: list[object],
        used_handles: tuple[str, ...] = (),
    ) -> dict[str, object]:
        query = payload.get("q")
        if type(query) is not list or len(query) != 5:
            raise AssertionError("compact phase/action contract shape changed")
        if code not in query[1]:
            raise AssertionError("fixture selected an action outside the live policy")
        return {
            "a": code,
            "c": cls._correlation(payload),
            "d": data,
            "u": list(used_handles),
        }

    def _domain_answer(
        self,
        binding: ProviderBinding,
        current: Mapping[str, Any],
        used_handles: tuple[str, ...],
    ) -> object:
        workflow = current["workflow"]
        if workflow == "conversation":
            return f"{_PRIVATE_SENTINEL} conversation reply 한🙂"
        if workflow == "conversation_retrieval":
            return [
                f"{_PRIVATE_SENTINEL} retrieval answer 🙂",
                list(range(1, len(used_handles) + 1)),
            ]

        phase = current["phase"]
        agent_id = binding.definition.agent_id
        if phase == Phase.AUDIT.value:
            audit_index = self._audit_count_by_agent.get(agent_id, 0)
            self._audit_count_by_agent[agent_id] = audit_index + 1
            revision = f"r{audit_index}"
            conflict = (
                agent_id == PLOT_ARCHITECT
                and revision in self.policy.conflicting_revisions
            )
            return (
                ["c", f"{_PRIVATE_SENTINEL} conflict {revision}"]
                if conflict
                else "a"
            )
        if phase == Phase.FINALIZE.value:
            return [
                [filename, content]
                for filename, content in self.policy.core_updates
            ]
        return (
            f"{_PRIVATE_SENTINEL} {self.policy.scenario_id} {phase} 한🙂"
        )

    def _response(
        self,
        binding: ProviderBinding,
        payload: Mapping[str, object],
        current: Mapping[str, Any],
    ) -> tuple[dict[str, object], str | None]:
        if current["workflow"] != "conversation_retrieval":
            answer = self._domain_answer(binding, current, ())
            return self._wire_action(payload, "a", [answer]), None

        self._retrieval_action_index += 1
        kind = self.policy.retrieval_actions[self._retrieval_action_index - 1]
        if kind == "search_conversations":
            return (
                self._wire_action(
                    payload,
                    "s",
                    [["source question", ["c"]]],
                ),
                kind,
            )

        if kind == "read_transcript":
            observation_kind, handles = _observation(payload.get("o"))
            if observation_kind != "search" or not handles:
                raise AssertionError("compact search produced no readable handle")
            return self._wire_action(payload, "r", list(handles)), kind
        if kind == "answer":
            if self._retrieval_action_index > 1:
                observation_kind, handles = _observation(payload.get("o"))
                if observation_kind != "read" or not handles:
                    raise AssertionError("compact read produced no cited evidence")
            else:
                handles = ()
            answer = self._domain_answer(binding, current, handles)
            return self._wire_action(payload, "a", [answer], handles), kind
        raise AssertionError(f"unknown retrieval fixture action: {kind}")

    @staticmethod
    def _semantic_invocation(
        invocation: ProviderInvocation,
        current: Mapping[str, Any] | None,
    ) -> _SemanticInvocation:
        if invocation.call_kind is ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ:
            return _SemanticInvocation(invocation.call_kind, invocation.payload)
        if current is None:
            raise AssertionError("substantive compact call omitted current material")
        if current["workflow"] == "conversation":
            call_kind = ProviderCallKind.CONVERSATION
            payload = invocation.payload
        elif current["workflow"] == "conversation_retrieval":
            call_kind = invocation.call_kind
            payload = invocation.payload
        else:
            call_kind = ProviderCallKind.DEBATE
            payload = {**invocation.payload, "phase": current["phase"]}
        return _SemanticInvocation(call_kind, payload)

    async def invoke(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        if binding.vendor != self.vendor or invocation.payload_json is None:
            raise AssertionError("optimized fixture received an invalid invocation")
        thread_id = self._thread_id(binding, invocation)
        current: dict[str, Any] | None = None
        action_kind: str | None = None
        if invocation.call_kind is ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ:
            payload = invocation.payload
            response: dict[str, object] = {
                "action": {
                    "kind": "read_writing_artifacts",
                    "correlation": payload["correlation"],
                    "references": payload["references"],
                }
            }
        else:
            payload = invocation.payload
            if "i" in payload:
                current = _current_material(payload)
                self._current_by_thread[thread_id] = current
            else:
                current = self._current_by_thread.get(thread_id)
                if current is None:
                    raise AssertionError("compact continuation lost current material")
            response, action_kind = self._response(binding, payload, current)

        semantic = self._semantic_invocation(invocation, current)
        elapsed = _CONTROLLED_ELAPSED_NS[semantic.call_kind]
        self.traces.append(
            _InvocationTrace(
                cast(ProviderInvocation, semantic),
                action_kind,
                elapsed,
            )
        )
        return ProviderResult(
            thread_id=thread_id,
            response_text=self._json(response),
            usage=None,
            actual_transport_utf8_bytes=len(invocation.payload_json),
        )


class _OptimizedHarness:
    """One isolated contextual runtime and its exact accounting records."""

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
            executable="optimized-provider-must-not-run",
        )
        test_case.addCleanup(self.backend.close)
        self.adapter = _DeterministicCompactAdapter(
            self.backend._codex_adapter,
            policy,
        )
        self.backend._adapters["codex"] = self.adapter
        self.records: list[ProviderCallAccounting] = []
        self.backend._context_accounting_sink = self.records.append
        self.backend.create_session_with_access(session_id)
        services = compose_filesystem_conversation_storage(self.workspace)
        self.catalog = services.catalog
        self.store = services.event_store
        self.runtime = StoryRuntime(
            self.workspace,
            self.backend,
            conversation_storage_services=services,
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


class _OptimizedBuilder(_BaselineBuilder):
    """Run the frozen workload through the contextual compact-wire runtime."""

    def _harness(
        self,
        scenario_id: str,
        *,
        retrieval_actions: tuple[str, ...] = (),
        conflicting_revisions: frozenset[str] = frozenset(),
        core_updates: tuple[tuple[str, str], ...] = (),
    ) -> _OptimizedHarness:
        return _OptimizedHarness(
            self.test_case,
            _RouterPolicy(
                scenario_id,
                retrieval_actions,
                conflicting_revisions,
                core_updates,
            ),
            session_id=f"session_{scenario_id}",
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
        search.assert_not_called()
        read.assert_not_called()
        self.test_case.assertEqual(len(result.sources), 1)
        self.test_case.assertEqual(
            result.sources[0].citation.conversation_id,
            source.conversation_id,
        )
        self.test_case.assertEqual(result.sources[0].citation.sequence, 1)
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

    def _report(self) -> dict[str, object]:
        report = super()._report()
        baseline = json.loads(BASELINE_GOLDEN_PATH.read_text(encoding="utf-8"))
        baseline_bytes = baseline["cumulative"]["byte_totals"][
            "non_reference_model_input_utf8_bytes"
        ]
        optimized_bytes = report["cumulative"]["byte_totals"][
            "non_reference_model_input_utf8_bytes"
        ]
        ceiling = baseline["later_reduction_ceiling"]
        report["schema_version"] = OPTIMIZED_REPORT_SCHEMA_VERSION
        report["quality_invariants"]["contextual_runtime"] = True
        report["quality_invariants"][
            "compact_wire_version"
        ] = OPTIMIZED_COMPACT_WIRE_VERSION
        report["baseline_comparison"] = {
            "baseline_fixture": BASELINE_GOLDEN_PATH.name,
            "baseline_non_reference_utf8_bytes": baseline_bytes,
            "maximum_optimized_utf8_bytes": ceiling[
                "maximum_candidate_utf8_bytes"
            ],
            "optimized_non_reference_utf8_bytes": optimized_bytes,
            "passes_50_percent_reduction": optimized_bytes * 2 <= baseline_bytes,
        }
        report["later_reduction_ceiling"] = ceiling
        return report


async def build_provider_context_optimized(
    test_case: TestCase,
) -> dict[str, object]:
    """Return the complete deterministic content-free wire-v5 report."""

    with deterministic_runtime_environment():
        return await _OptimizedBuilder(test_case).build()
