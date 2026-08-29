"""End-to-end acceptance coverage for contextual story-runtime adoption."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from story_writing_agents import (
    AGENT_SPECS,
    AgentBackend,
    BackendProtocolError,
    BoundAgent,
    CHARACTER_CUSTODIAN,
    ConversationReply,
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
    ProviderUsage,
    canonical_provider_json,
)
from story_writing_agents.agent_context.ledger import (
    ContextLedger,
    ContextLedgerKey,
    ContextLedgerRecordState,
)
from story_writing_agents.agent_context.manifest import ContextSubjectKind
from story_writing_agents.agent_context import service as service_module
from story_writing_agents.agent_context.planning import (
    ContextCheckpointState,
    ContextDeliveryMode,
)
from story_writing_agents.agent_context.service import (
    AgentContextService,
    ContextExecutionEvent,
    ContextExecutionEventKind,
)
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

from tests.helpers import ScriptedBackend, initialized_workspace


SearchSelection = (
    tuple[tuple[str, ...], str]
    | tuple[str, tuple[str, ...], str]
)
_SOURCE_KIND_CODES = {
    "writing_artifact": "w",
    "conversation": "c",
    "active_debate": "d",
    "own_agent_state": "o",
}


def _framed_observation(value: object) -> dict[str, Any]:
    """Normalize one authoritative compact wire-v5 observation."""

    def source_facts(raw: object) -> dict[str, object]:
        if (
            not isinstance(raw, list)
            or len(raw) != 6
            or raw[0] != "source"
            or not isinstance(raw[4], list)
        ):
            raise AssertionError("context observation omitted source facts")
        digest_facts = raw[4]
        if len(digest_facts) == 2 and digest_facts[0] == "revision=source_digest":
            revision = source_digest = digest_facts[1]
        elif (
            len(digest_facts) == 4
            and digest_facts[0] == "revision"
            and digest_facts[2] == "source_digest"
        ):
            revision, source_digest = digest_facts[1], digest_facts[3]
        else:
            raise AssertionError("context observation has invalid source digests")
        return {
            "source_alias": raw[1],
            "source_id": raw[2],
            "source_kind_full": raw[3],
            "revision": revision,
            "source_digest": source_digest,
            "source_utf8_bytes": raw[5],
        }

    def boundary_facts(raw: object) -> dict[str, object]:
        if not isinstance(raw, list) or len(raw) != 8 or raw[0] != "boundary":
            raise AssertionError("context observation omitted boundary facts")
        return {
            "kind": raw[1],
            "start": raw[2],
            "end": raw[3],
            "first_id": raw[4],
            "last_id": raw[5],
            "digest": raw[6],
            "utf8_bytes": raw[7],
        }

    def provenance_facts(raw: object) -> dict[str, object]:
        if (
            not isinstance(raw, list)
            or len(raw) != 3
            or raw[0] != "provenance"
        ):
            raise AssertionError("context observation omitted provenance facts")
        return {"authority_kind": raw[1], "locator": raw[2]}

    if not isinstance(value, list) or not value:
        raise AssertionError("context continuation omitted its observation")
    if value[0] == "s" and len(value) == 3 and isinstance(value[2], list):
        return {
            "truncated": value[1],
            "hits": [
                {
                    "query_index": item[0],
                    **source_facts(item[1]),
                    "boundary": boundary_facts(item[2]),
                    "provenance": provenance_facts(item[3]),
                    "locator": provenance_facts(item[3])["locator"],
                    "source_kind": item[4],
                    "handle": item[5],
                    "snippet": item[6][1],
                    "snippet_utf8_bytes": item[6][2],
                }
                for item in value[2]
            ],
        }
    if value[0] == "r" and len(value) == 2 and isinstance(value[1], list):
        return {
            "evidence": [
                {
                    **source_facts(item[0]),
                    "boundary": boundary_facts(item[1]),
                    "provenance": provenance_facts(item[2]),
                    "locator": provenance_facts(item[2])["locator"],
                    "source_kind": item[3],
                    "handle": item[4],
                    "content": item[5],
                }
                for item in value[1]
            ],
        }
    raise AssertionError("context observation had an unknown compact shape")


def _current_material(
    payload: Mapping[str, object],
    phase: str,
) -> dict[str, Any]:
    current = payload.get("i")
    if isinstance(current, dict):
        value = dict(current)
        if "workflow" in value:
            return value
        if phase == "retrieval" and isinstance(value.get("input"), str):
            return {
                "workflow": "conversation_retrieval",
                "command": value["input"],
                "rag_context": value.get("rag"),
            }
    if isinstance(current, str):
        return {
            "workflow": (
                "conversation_retrieval"
                if phase == "retrieval"
                else "conversation"
                if phase == "conversation"
                else "debate"
            ),
            "command": current,
        }
    raise AssertionError("initial context call omitted current request material")


class _CandidateWireInterpreter:
    """Deterministically apply the provider-visible ``n``/``p``/``t`` contract."""

    def __init__(self) -> None:
        self.candidate: str | None = None
        self.records: dict[tuple[int, int], str] = {}

    @staticmethod
    def _digest(value: str) -> str:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _facts(cls, raw: object) -> tuple[tuple[int, int], str, int]:
        if type(raw) is not dict or set(raw) != {
            "source_alias",
            "record_alias",
            "digest",
            "utf8_bytes",
        }:
            raise AssertionError("candidate record facts are malformed")
        source_alias = raw["source_alias"]
        record_alias = raw["record_alias"]
        digest = raw["digest"]
        utf8_bytes = raw["utf8_bytes"]
        if (
            type(source_alias) is not int
            or source_alias < 1
            or type(record_alias) is not int
            or record_alias < 1
            or type(digest) is not str
            or type(utf8_bytes) is not int
            or utf8_bytes < 0
        ):
            raise AssertionError("candidate record facts are invalid")
        return (source_alias, record_alias), digest, utf8_bytes

    @classmethod
    def _verify(cls, value: str, facts: object) -> tuple[int, int]:
        key, digest, utf8_bytes = cls._facts(facts)
        encoded = value.encode("utf-8")
        if cls._digest(value) != digest or len(encoded) != utf8_bytes:
            raise AssertionError("candidate record facts do not match content")
        return key

    def _resolve_candidate(self, raw: object) -> str:
        if type(raw) is str:
            return raw
        if type(raw) is not dict:
            raise AssertionError("candidate operation is malformed")
        common = {
            "op",
            "base",
            "target",
            "result",
            "resolve_record_candidate",
        }
        operation = raw.get("op")
        if operation == "candidate_pointer":
            if set(raw) != common:
                raise AssertionError("candidate pointer fields are malformed")
        elif operation == "candidate_single_splice":
            if set(raw) != common | {
                "prefix_codepoints",
                "replacement",
                "suffix_codepoints",
            }:
                raise AssertionError("candidate splice fields are malformed")
        else:
            raise AssertionError("candidate operation is unknown")
        if (
            raw["result"] != "candidate_text"
            or raw["resolve_record_candidate"] != "logical_n"
        ):
            raise AssertionError("candidate operation result contract changed")
        base_key, _digest_value, _bytes_value = self._facts(raw["base"])
        base = self.records.get(base_key)
        if base is None:
            raise AssertionError("candidate base record is unavailable")
        self._verify(base, raw["base"])
        if operation == "candidate_pointer":
            result = base
        else:
            prefix = raw["prefix_codepoints"]
            suffix = raw["suffix_codepoints"]
            replacement = raw["replacement"]
            if (
                type(prefix) is not int
                or type(suffix) is not int
                or prefix < 0
                or suffix < 0
                or type(replacement) is not str
                or prefix + suffix > len(base)
            ):
                raise AssertionError("candidate splice bounds are invalid")
            retained_suffix = base[len(base) - suffix :] if suffix else ""
            result = base[:prefix] + replacement + retained_suffix
        self._verify(result, raw["target"])
        return result

    def apply(self, payload: Mapping[str, object]) -> str | None:
        """Apply top-level candidate, record upserts, then tombstones in order."""

        if "n" in payload:
            self.candidate = self._resolve_candidate(payload["n"])
        projected = payload.get("p", [])
        if type(projected) is not list:
            raise AssertionError("candidate record patch is malformed")
        for group in projected:
            if (
                type(group) is not list
                or len(group) < 3
                or group[0] not in {"c", "d", "o"}
                or type(group[1]) is not int
                or type(group[-1]) is not list
            ):
                raise AssertionError("candidate record group is malformed")
            source_alias = group[1]
            for record in group[-1]:
                if type(record) is not list or not record:
                    raise AssertionError("candidate record is malformed")
                key = (source_alias, record[0])
                if (
                    len(record) == 8
                    and record[1] == "m"
                    and record[6] == "candidate"
                ):
                    content = record[7]
                    if content == ["=", "k"]:
                        if self.candidate is None:
                            raise AssertionError("candidate pointer precedes n")
                        content = self.candidate
                    if type(content) is not str:
                        raise AssertionError("candidate record content is malformed")
                    self.records[key] = content
                else:
                    self.records.pop(key, None)
        tombstones = payload.get("t", [])
        if type(tombstones) is not list:
            raise AssertionError("candidate tombstones are malformed")
        for tombstone in tombstones:
            if type(tombstone) is not list:
                raise AssertionError("candidate tombstone is malformed")
            if len(tombstone) == 3 and tombstone[0] == "r":
                self.records.pop((tombstone[1], tombstone[2]), None)
            elif len(tombstone) == 2 and tombstone[0] in {"s", "d"}:
                source_alias = tombstone[1]
                self.records = {
                    key: value
                    for key, value in self.records.items()
                    if key[0] != source_alias
                }
            else:
                raise AssertionError("candidate tombstone is malformed")
        return self.candidate


class _ContextAdapterHarness:
    """Answer real backend contextual calls while retaining exact wire facts."""

    def __init__(self) -> None:
        self.invocations: list[ProviderInvocation] = []
        self.bindings: list[ProviderBinding] = []
        self.context_payloads: list[Mapping[str, object]] = []
        self.context_invocations: list[ProviderInvocation] = []
        self.reference_payloads: list[Mapping[str, object]] = []
        self.search_frames: list[dict[str, Any]] = []
        self.read_frames: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.current_by_thread: dict[str, dict[str, Any]] = {}
        self.candidate_interpreters: dict[str, _CandidateWireInterpreter] = {}
        self.candidate_wire: list[tuple[str, Mapping[str, object], str | None]] = []
        self.current_history: list[dict[str, Any]] = []
        self.action_count_by_thread: dict[str, int] = {}
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
        self.input_tokens: int | None = None
        self.mutate_before_answer: Callable[[dict[str, Any]], None] | None = None
        self.conflicts: set[tuple[str, str, str]] = set()
        self.final_updates: dict[str, dict[str, str]] = {}
        self.candidates: dict[str, str] = {}
        self.candidate_selector: Callable[[dict[str, Any]], str] | None = None
        self.active_debate_id: str | None = None
        self.revision_by_debate: dict[str, int] = {}
        self.last_phase_by_debate: dict[str, str] = {}
        self.block_entered = asyncio.Event()
        self.block_release = asyncio.Event()
        self._next_thread = 1

    @staticmethod
    def _correlation(payload: Mapping[str, object]) -> str:
        correlation = payload.get("c")
        if not isinstance(correlation, str):
            raise AssertionError("context call omitted correlation")
        return correlation

    def _thread_id(self, invocation: ProviderInvocation) -> str:
        if invocation.thread_id is not None:
            return invocation.thread_id
        value = f"context-thread-{self._next_thread}"
        self._next_thread += 1
        return value

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _search_action(
        cls,
        correlation: str,
        selection: SearchSelection,
    ) -> str:
        if len(selection) == 2:
            source_kinds, text = selection
            data: list[object] = [
                [text, [_SOURCE_KIND_CODES[item] for item in source_kinds]]
            ]
        else:
            artifact_path_prefix, source_kinds, text = selection
            data = [
                artifact_path_prefix,
                [text, [_SOURCE_KIND_CODES[item] for item in source_kinds]],
            ]
        return cls._json(
            {"a": "s", "c": correlation, "d": data, "u": []}
        )

    def _debate_identity(
        self,
        binding: ProviderBinding,
        phase: str,
    ) -> tuple[str, str]:
        known = [*self.final_updates, *self.candidates]
        known.extend(
            debate_id for debate_id, _revision, _agent in self.conflicts
        )
        debate_id = self.active_debate_id or (
            known[-1] if known else "context_reference_debate"
        )
        previous_phase = self.last_phase_by_debate.get(debate_id)
        revision = self.revision_by_debate.setdefault(debate_id, 0)
        if phase == "propose_revision" and previous_phase != phase:
            revision += 1
            self.revision_by_debate[debate_id] = revision
        self.last_phase_by_debate[debate_id] = phase
        return debate_id, f"r{revision}"

    def _domain_answer(
        self,
        current: dict[str, Any],
        used_handles: tuple[str, ...],
    ) -> object:
        workflow = current["workflow"]
        if workflow == "conversation":
            domain: object = f"contextual reply: {current['command']}"
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
            context_ids: list[object] = list(
                range(1, len(used_handles) + 1)
            )
            if self.invalid_citation_selector(current):
                context_ids = [999]
            domain = [
                f"retrieved reply: {current['command']}",
                [*rag_ids, *context_ids],
            ]
        else:
            identity = current["identity"]
            debate_id = identity["debate_id"]
            revision = current["revision"]
            phase = current["phase"]
            agent_id = identity["agent_id"]
            if phase == "audit":
                conflict = (debate_id, revision, agent_id) in self.conflicts
                domain = ["c", "conflict requires revision"] if conflict else "a"
            elif phase == "finalize":
                domain = [
                    [filename, content]
                    for filename, content in self.final_updates.get(
                        debate_id, {}
                    ).items()
                ]
            else:
                content = (
                    self.candidate_selector(current)
                    if self.candidate_selector is not None
                    else self.candidates.get(
                        debate_id,
                        f"candidate:{debate_id}:{revision}:{phase}",
                    )
                )
                domain = content
        if self.invalid_domain_selector(current):
            return []
        return domain

    def _answer_action(
        self,
        current: dict[str, Any],
        correlation: str,
        used_handles: tuple[str, ...] = (),
    ) -> str:
        if self.mutate_before_answer is not None:
            mutation = self.mutate_before_answer
            self.mutate_before_answer = None
            mutation(current)
        result = self._domain_answer(current, used_handles)
        action = {"a": "a", "c": correlation, "d": [result], "u": list(used_handles)}
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
                usage=(
                    None
                    if self.input_tokens is None
                    else ProviderUsage(input_tokens=self.input_tokens)
                ),
                actual_transport_utf8_bytes=len(invocation.payload_json or b""),
            )

        correlation = self._correlation(payload)
        thread_id = self._thread_id(invocation)
        action_index = self.action_count_by_thread.get(thread_id, 0) + 1
        self.action_count_by_thread[thread_id] = action_index
        if "i" in payload:
            self.context_payloads.append(payload)
            self.context_invocations.append(invocation)
            interpreter = self.candidate_interpreters.setdefault(
                thread_id,
                _CandidateWireInterpreter(),
            )
            reconstructed_candidate = interpreter.apply(payload)
            if any(key in payload for key in ("n", "p", "t")):
                self.candidate_wire.append(
                    (thread_id, payload, reconstructed_candidate)
                )
            query = payload.get("q")
            if not isinstance(query, list) or not isinstance(query[0], str):
                raise AssertionError("context call omitted phase query")
            current = _current_material(payload, query[0])
            if current["workflow"] == "debate":
                debate_id, revision = self._debate_identity(
                    binding,
                    query[0],
                )
                current.update(
                    {
                        "identity": {
                            "debate_id": debate_id,
                            "agent_id": binding.definition.agent_id,
                        },
                        "phase": query[0],
                        "revision": revision,
                    }
                )
            self.current_by_thread[thread_id] = current
            self.current_history.append(current)
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
                response = self._search_action(correlation, search)
        else:
            current = self.current_by_thread[thread_id]
            frame = _framed_observation(payload.get("o"))
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
                    response = self._search_action(correlation, selection)
                else:
                    response = self._json(
                        {
                            "a": "r",
                            "c": correlation,
                            "d": list(handles),
                            "u": [],
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
                    response = self._search_action(correlation, selection)
                else:
                    response = self._answer_action(current, correlation, handles)
            else:
                raise AssertionError("unexpected contextual observation")
        return ProviderResult(
            thread_id,
            response,
            usage=(
                None
                if self.input_tokens is None
                else ProviderUsage(input_tokens=self.input_tokens)
            ),
            actual_transport_utf8_bytes=len(invocation.payload_json or b""),
        )


class CandidateWireContractTests(unittest.TestCase):
    """Prove the named candidate operations are reconstructable and fail closed."""

    @staticmethod
    def facts(source_alias: int, record_alias: int, content: str) -> dict[str, object]:
        encoded = content.encode("utf-8")
        return {
            "source_alias": source_alias,
            "record_alias": record_alias,
            "digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "utf8_bytes": len(encoded),
        }

    @staticmethod
    def record(record_alias: int, content: object) -> list[object]:
        return [
            record_alias,
            "m",
            "execution_specialist",
            "draft",
            0,
            "r0",
            "candidate",
            content,
        ]

    @classmethod
    def operation(
        cls,
        base: str,
        target: str,
        *,
        base_alias: int,
        target_alias: int,
    ) -> dict[str, object]:
        prefix = 0
        while prefix < min(len(base), len(target)) and base[prefix] == target[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < len(base) - prefix
            and suffix < len(target) - prefix
            and base[len(base) - suffix - 1] == target[len(target) - suffix - 1]
        ):
            suffix += 1
        replacement_end = len(target) - suffix if suffix else len(target)
        return {
            "op": "candidate_single_splice",
            "base": cls.facts(1, base_alias, base),
            "prefix_codepoints": prefix,
            "replacement": target[prefix:replacement_end],
            "suffix_codepoints": suffix,
            "target": cls.facts(1, target_alias, target),
            "result": "candidate_text",
            "resolve_record_candidate": "logical_n",
        }

    def test_interpreter_applies_full_pointer_splices_upserts_and_tombstones(self) -> None:
        interpreter = _CandidateWireInterpreter()
        first = ("A🙂\ue000\u0378\n" * 700) + "unsectioned tail"
        self.assertEqual(
            interpreter.apply(
                {
                    "n": first,
                    "p": [["d", 1, [self.record(1, ["=", "k"])]]],
                }
            ),
            first,
        )
        self.assertEqual(interpreter.records[(1, 1)], first)

        pointer = {
            "op": "candidate_pointer",
            "base": self.facts(1, 1, first),
            "target": self.facts(1, 2, first),
            "result": "candidate_text",
            "resolve_record_candidate": "logical_n",
        }
        self.assertEqual(
            interpreter.apply(
                {
                    "n": pointer,
                    "p": [["d", 1, [self.record(2, ["=", "k"])]]],
                }
            ),
            first,
        )
        self.assertEqual(interpreter.records[(1, 2)], first)

        second = first.replace("unsectioned tail", "reordered Δ tail")
        first_to_second = self.operation(
            first,
            second,
            base_alias=2,
            target_alias=3,
        )
        self.assertEqual(
            interpreter.apply(
                {
                    "n": first_to_second,
                    "p": [["d", 1, [self.record(3, ["=", "k"])]]],
                }
            ),
            second,
        )
        third = "prefix inserted\n" + second[:-20] + "deleted and moved🙂"
        second_to_third = self.operation(
            second,
            third,
            base_alias=3,
            target_alias=4,
        )
        self.assertEqual(
            interpreter.apply(
                {
                    "n": second_to_third,
                    "p": [
                        [
                            "d",
                            1,
                            [
                                self.record(3, "updated existing candidate"),
                                self.record(4, ["=", "k"]),
                            ],
                        ]
                    ],
                    "t": [["r", 1, 3]],
                }
            ),
            third,
        )
        self.assertNotIn((1, 3), interpreter.records)
        self.assertEqual(interpreter.records[(1, 4)], third)
        self.assertEqual(interpreter.apply({"p": []}), third)
        self.assertEqual(interpreter.records[(1, 4)], third)
        interpreter.apply({"t": [["d", 1]]})
        self.assertEqual(interpreter.records, {})

    def test_interpreter_rejects_stale_or_tampered_operations(self) -> None:
        base = "base🙂" * 500
        target = base + "changed"

        def prepared() -> _CandidateWireInterpreter:
            current = _CandidateWireInterpreter()
            current.apply(
                {
                    "n": base,
                    "p": [["d", 1, [self.record(1, ["=", "k"])]]],
                }
            )
            return current

        valid = self.operation(
            base,
            target,
            base_alias=1,
            target_alias=2,
        )
        invalid: list[object] = [
            {**valid, "extra": True},
            {**valid, "result": "other"},
            {**valid, "resolve_record_candidate": "other"},
            {**valid, "base": self.facts(1, 99, base)},
            {
                **valid,
                "base": {**self.facts(1, 1, base), "digest": "sha256:" + "0" * 64},
            },
            {
                **valid,
                "target": {**self.facts(1, 2, target), "utf8_bytes": 1},
            },
            {**valid, "prefix_codepoints": len(base) + 1},
            {**valid, "suffix_codepoints": len(base) + 1},
            {**valid, "replacement": 1},
            {"op": "legacy_candidate_patch"},
        ]
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(
                AssertionError
            ):
                prepared().apply({"n": operation})
        with self.assertRaises(AssertionError):
            _CandidateWireInterpreter().apply(
                {"p": [["d", 1, [self.record(1, ["=", "k"])]]]}
            )

    def test_production_candidate_operation_bytes_and_full_fallbacks_are_exact(self) -> None:
        def record(alias: int, content: str) -> object:
            encoded = content.encode("utf-8")
            return service_module._WireCandidateRecord(
                "s1",
                alias,
                content,
                "sha256:" + hashlib.sha256(encoded).hexdigest(),
                len(encoded),
            )

        projection = SimpleNamespace(
            mode=ContextDeliveryMode.DELTA,
            checkpoint_proposal=SimpleNamespace(sources=(), catalog_sources=()),
        )
        checkpoint = SimpleNamespace(
            state=ContextCheckpointState.READY,
            provider_thread_id="thread-v5",
            sources=(),
            catalog_sources=(),
        )
        base = ("section🙂\n" * 800) + "tail A"
        targets = (
            base,
            base.replace("tail A", "tail B with Δ"),
            "new lead\n" + base,
            base[:-30] + "arbitrary unsectioned splice",
        )
        for index, target in enumerate(targets, start=2):
            with self.subTest(target=index):
                with mock.patch.object(
                    service_module,
                    "_latest_debate_candidate_record",
                    side_effect=(record(1, base), record(index, target)),
                ):
                    actual = service_module._wire_candidate_value(
                        target,
                        projection,
                        checkpoint,
                        (),
                    )
                expected: object
                if target == base:
                    expected = {
                        "op": "candidate_pointer",
                        "base": self.facts(1, 1, base),
                        "target": self.facts(1, index, target),
                        "result": "candidate_text",
                        "resolve_record_candidate": "logical_n",
                    }
                else:
                    expected = self.operation(
                        base,
                        target,
                        base_alias=1,
                        target_alias=index,
                    )
                self.assertEqual(actual, expected)
                self.assertEqual(
                    canonical_provider_json(actual),
                    canonical_provider_json(expected),
                )
                interpreter = _CandidateWireInterpreter()
                interpreter.apply(
                    {"n": base, "p": [["d", 1, [self.record(1, ["=", "k"])]]]}
                )
                self.assertEqual(interpreter.apply({"n": actual}), target)

        short = "short"
        with mock.patch.object(
            service_module,
            "_latest_debate_candidate_record",
            side_effect=(record(1, short), record(2, short)),
        ):
            self.assertEqual(
                service_module._wire_candidate_value(
                    short,
                    projection,
                    checkpoint,
                    (),
                ),
                short,
            )
        with mock.patch.object(
            service_module,
            "_latest_debate_candidate_record",
            side_effect=(record(1, base), record(2, base + "stale")),
        ):
            self.assertEqual(
                service_module._wire_candidate_value(
                    base,
                    projection,
                    checkpoint,
                    (),
                ),
                base,
            )
        for mode, state, thread in (
            (ContextDeliveryMode.BOOTSTRAP, ContextCheckpointState.READY, "thread"),
            (ContextDeliveryMode.DELTA, ContextCheckpointState.MISSING, None),
            (ContextDeliveryMode.DELTA, ContextCheckpointState.CORRUPT, None),
            (ContextDeliveryMode.DELTA, ContextCheckpointState.TAINTED, None),
            (ContextDeliveryMode.DELTA, ContextCheckpointState.STALE, None),
            (ContextDeliveryMode.DELTA, ContextCheckpointState.READY, None),
        ):
            with self.subTest(mode=mode, state=state, thread=thread):
                fallback_projection = SimpleNamespace(mode=mode)
                fallback_checkpoint = SimpleNamespace(
                    state=state,
                    provider_thread_id=thread,
                )
                self.assertEqual(
                    service_module._wire_candidate_value(
                        base,
                        fallback_projection,
                        fallback_checkpoint,
                        (),
                    ),
                    base,
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
        self.assertEqual(
            provider.context_payloads[0]["q"],
            [
                "conversation",
                "sa",
                "wco",
                [4, 2, 2, 4, 8, 8, 16, 200],
                "text",
            ],
        )
        self.assertEqual(
            provider.context_payloads[1]["q"],
            [
                "retrieval",
                "sa",
                "c",
                [4, 2, 2, 4, 8, 8, 16, 200],
                ["retrieval", "text", "citations"],
            ],
        )

        for payload in provider.context_payloads:
            self.assertNotIn("available_sources", payload)
            self.assertNotIn("projected_units", payload)
            self.assertEqual(payload["q"][0], (
                "retrieval" if payload is provider.context_payloads[1] else "conversation"
            ))
            self.assertNotIn("docs", payload)

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
            self.assertEqual(
                payload["i"] if isinstance(payload["i"], str) else payload["i"]["input"],
                pending_text,
            )
            conversation_groups = [
                group for group in payload.get("p", []) if group[0] == "c"
            ]
            self.assertEqual(len(conversation_groups), 1)
            self.assertEqual(conversation_groups[0][4], [])

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
        self.assertIsNotNone(provider.context_invocations[2].thread_id)
        self.assertEqual(
            [item[1:] for item in delta["docs"]],
            [["core_documents/global_outline.md", "LATE_ROLE_CORE_MARKER"]],
        )
        self.assertIs(type(delta["docs"][0][0]), int)
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
            expected_core_paths = {
                f"core_documents/{filename}"
                for filename in AGENT_SPECS[agent_id].context_documents
            }
            self.assertIsNone(self.provider.context_invocations[-1].thread_id)
            self.assertTrue(
                expected_core_paths.issubset(
                    {item[1] for item in first_payload.get("docs", [])}
                )
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
            self.assertIsNotNone(self.provider.context_invocations[-1].thread_id)
            self.assertFalse(
                expected_core_paths.issubset(
                    {item[1] for item in second_payload.get("docs", [])}
                )
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
            self.assertIsNotNone(self.provider.context_invocations[-1].thread_id)
            self.assertTrue(
                any(
                    item[1] == f"core_documents/{changed_filename}"
                    and item[2] == changed_token
                    for item in changed_payload.get("docs", [])
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
            self.assertIsNone(self.provider.context_invocations[-1].thread_id)

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
            side_effect=AssertionError("non-contextual conversation route selected"),
        ) as direct_converse:
            first = await self.runtime.converse(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "PRIVATE_VISIBLE_FIRST_COMMAND",
            )
        direct_converse.assert_not_awaited()
        self.assertEqual(first.agent_id, PLOT_ARCHITECT)
        events = self.services.event_store.read(session_id, conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertNotIn(
            "available_sources",
            self.provider.context_payloads[-1],
        )
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
        self.assertIsNotNone(self.provider.context_invocations[-1].thread_id)

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
        self.assertIsNone(self.provider.context_invocations[-1].thread_id)

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
            side_effect=AssertionError("non-contextual retrieval route selected"),
        ) as direct_retrieval:
            result = await self.runtime.converse_with_retrieval(
                session_id,
                selected_id,
                PLOT_ARCHITECT,
                "retrieve stored evidence",
                memory_service=memory,
                progress_sink=progress.append,
            )
        direct_retrieval.assert_not_awaited()
        retrieval_query = self.provider.context_payloads[-1]["q"]
        self.assertEqual(retrieval_query[0], "retrieval")
        self.assertEqual(retrieval_query[2], "c")
        self.assertEqual(
            retrieval_query[4],
            ["retrieval", "text", "citations"],
        )
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
            locator = evidence["locator"]
            conversation_id = locator.removeprefix("conversation/")
            if conversation_id not in expected_conversations:
                expected_conversations.append(conversation_id)
        self.assertEqual(
            tuple(source.citation.conversation_id for source in result.sources),
            tuple(expected_conversations),
        )
        self.assertEqual(
            self.provider.answers[-1]["u"],
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
            self.provider.answers[-1]["d"][0][1],
            ["g1", 1],
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
            side_effect=AssertionError("non-contextual debate route selected"),
        ) as direct_invoke:
            result = await self.runtime.run_prompt(
                session_id,
                "create a contextual candidate",
                debate_id=debate_id,
                output_path="part-01/context-consensus.md",
                max_debate_rounds=2,
            )
        direct_invoke.assert_not_awaited()
        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.rounds_completed, 2)
        self.assertEqual(result.revision_id, "r2")
        self.assertEqual(
            self.workspace.read_text("manuscript/part-01/context-consensus.md"),
            result.final_candidate,
        )
        currents = [
            current
            for current in self.provider.current_history
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
        ]
        phases = {
            payload["q"][0]
            for payload in self.provider.context_payloads
            if payload["q"][0]
            in {"draft", "audit", "propose_revision", "synthesize", "finalize"}
        }
        self.assertEqual(
            phases,
            {"draft", "audit", "propose_revision", "synthesize", "finalize"},
        )
        debate_queries = [
            payload["q"]
            for payload in self.provider.context_payloads
            if payload["q"][0] in phases
        ]
        self.assertTrue(all(query[2] == "wcdo" for query in debate_queries))
        expected_contracts = {
            "draft": "text",
            "audit": ["audit", "approve-or-conflict"],
            "propose_revision": "text",
            "synthesize": "text",
            "finalize": ["finalize", "path-and-replacement-list"],
        }
        self.assertTrue(
            all(query[4] == expected_contracts[query[0]] for query in debate_queries)
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

    async def test_real_debate_candidate_wire_reconstructs_full_pointer_and_splice(
        self,
    ) -> None:
        session_id = "candidate_wire_session"
        debate_id = "candidate_wire_debate"
        first = ("opening A🙂\n" * 650) + "original ending"
        second = first.replace("original ending", "revised ending Δ")
        self.ready(session_id)
        self.provider.conflicts.add((debate_id, "r0", PLOT_ARCHITECT))
        self.provider.candidate_selector = lambda current: (
            first if current["revision"] == "r0" else second
        )

        result = await self.runtime.run_prompt(
            session_id,
            "exercise long candidate context",
            debate_id=debate_id,
            output_path="part-01/candidate-wire.md",
            max_debate_rounds=2,
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.final_candidate, second)
        candidate_deliveries = [
            (payload["n"], reconstructed)
            for _thread, payload, reconstructed in self.provider.candidate_wire
            if "n" in payload
        ]
        self.assertTrue(candidate_deliveries)
        self.assertTrue(
            all(reconstructed in {first, second} for _wire, reconstructed in candidate_deliveries)
        )
        operations = [
            wire
            for wire, _reconstructed in candidate_deliveries
            if isinstance(wire, Mapping)
        ]
        self.assertTrue(
            any(operation.get("op") == "candidate_pointer" for operation in operations)
        )
        self.assertTrue(
            any(
                operation.get("op") == "candidate_single_splice"
                for operation in operations
            )
        )
        for operation in operations:
            self.assertEqual(operation["result"], "candidate_text")
            self.assertEqual(
                operation["resolve_record_candidate"],
                "logical_n",
            )
        self.assertTrue(
            any(
                '["=","k"]' in json.dumps(
                    payload.get("p", []),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for _thread, payload, _candidate in self.provider.candidate_wire
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
            for current in self.provider.current_by_thread.values()
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
                    for payload, current in zip(
                        self.provider.context_payloads,
                        self.provider.current_history,
                        strict=True,
                    )
                    if (
                        current["workflow"] == "debate"
                        and current["identity"]["debate_id"] == debate_id
                        and current["identity"]["agent_id"] == responsible_agent
                    )
                ]
                self.assertTrue(relevant_payloads)
                source_entries = [
                    doc
                    for payload in relevant_payloads
                    for doc in payload.get("docs", [])
                    if doc[1] == f"core_documents/{filename}"
                ]
                self.assertTrue(source_entries)
                self.assertTrue(all(entry[2] for entry in source_entries))

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
            if payload["q"][0]
            in {"draft", "audit", "propose_revision", "synthesize", "finalize"}
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
            self.assertNotIn("mandatory_references", payload)
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
            self.provider.active_debate_id = debate_id
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
        self.assertEqual(
            budget_outcomes.count("context_search_completed"),
            2,
        )
        self.assertIn("context_tainted", budget_outcomes)
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

    async def test_live_ordinary_bootstrap_delta_search_and_privacy_are_transient(
        self,
    ) -> None:
        session_id = "live_context_ordinary"
        self.ready(session_id)
        conversation_id = self.conversation(session_id, "Live context")
        self.provider.input_tokens = 17
        private_prompt = "PRIVATE_LIVE_CONTEXT_PROMPT_한🙂"

        bootstrap: list[ContextExecutionEvent] = []
        calls_before = len(self.provider.invocations)
        first = await self.runtime.converse(
            session_id,
            conversation_id,
            PLOT_ARCHITECT,
            private_prompt,
            context_event_sink=bootstrap.append,
        )
        self.assertEqual(len(self.provider.invocations) - calls_before, 1)
        self.assertIs(bootstrap[0].kind, ContextExecutionEventKind.BOOTSTRAP)
        self.assertEqual(
            tuple(event.kind for event in bootstrap[-2:]),
            (
                ContextExecutionEventKind.ANSWER_AUTHORIZED,
                ContextExecutionEventKind.CHECKPOINT_ADVANCED,
            ),
        )
        self.assertEqual(bootstrap[-1].provider_input_tokens, 17)

        delta: list[ContextExecutionEvent] = []
        calls_before = len(self.provider.invocations)
        second = await self.runtime.converse(
            session_id,
            conversation_id,
            PLOT_ARCHITECT,
            "follow-up without search",
            context_event_sink=delta.append,
        )
        self.assertEqual(len(self.provider.invocations) - calls_before, 1)
        self.assertIs(delta[0].kind, ContextExecutionEventKind.DELTA)
        self.assertEqual(delta[-1].provider_input_tokens, 17)
        self.assertEqual(first.agent_id, second.agent_id)
        self.assertEqual(
            tuple(event.kind for event in self.services.event_store.read(session_id, conversation_id)),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        stored = self.services.event_store.read(session_id, conversation_id)
        self.assertEqual(sum(event.content == private_prompt for event in stored), 1)
        self.assertTrue(all(private_prompt not in repr(event) for event in bootstrap + delta))
        debug_text = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(encoding="utf-8")
        self.assertNotIn(private_prompt, debug_text)

        searchable = self.workspace.path("core_documents/narrative_rules.md")
        searchable.write_text("LIVE_ARTIFACT_SEARCH_NEEDLE\n", encoding="utf-8")
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), "LIVE_ARTIFACT_SEARCH_NEEDLE")
            if current.get("workflow") == "conversation"
            and current.get("command") == "search the canonical artifacts"
            else None
        )
        search_conversation = self.conversation(session_id, "Live search")
        search_events: list[ContextExecutionEvent] = []
        await self.runtime.converse(
            session_id,
            search_conversation,
            PLOT_ARCHITECT,
            "search the canonical artifacts",
            context_event_sink=search_events.append,
        )
        search_kinds = [event.kind for event in search_events]
        self.assertEqual(search_kinds.count(ContextExecutionEventKind.SEARCH_STARTED), 1)
        self.assertEqual(search_kinds.count(ContextExecutionEventKind.SEARCH_COMPLETED), 1)
        self.assertEqual(search_kinds.count(ContextExecutionEventKind.READ_STARTED), 1)
        self.assertEqual(search_kinds.count(ContextExecutionEventKind.READ_COMPLETED), 1)
        self.assertEqual(search_events[-1].provider_input_tokens, 51)

    async def test_retrieval_emits_generic_and_established_progress_once_each(
        self,
    ) -> None:
        session_id = "live_context_retrieval"
        self.ready(session_id)
        selected = self.conversation(session_id, "Selected")
        source = self.conversation(session_id, "Source")
        self.append_turn(
            session_id,
            source,
            "source-turn",
            "LIVE_RETRIEVAL_NEEDLE question",
            "LIVE_RETRIEVAL_NEEDLE answer",
        )
        self.provider.search_selector = lambda current: (
            (("conversation",), "LIVE_RETRIEVAL_NEEDLE")
            if current.get("workflow") == "conversation_retrieval"
            else None
        )
        memory = ConversationMemoryService(
            ConversationSearchScope(session_id),
            self.services.catalog,
            self.services.event_store,
        )
        established: list[object] = []
        generic: list[ContextExecutionEvent] = []
        result = await self.runtime.converse_with_retrieval(
            session_id,
            selected,
            PLOT_ARCHITECT,
            "retrieve live evidence",
            memory_service=memory,
            progress_sink=established.append,
            context_event_sink=generic.append,
        )
        self.assertTrue(result.sources)
        self.assertEqual(
            [event.phase for event in established],
            [
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                ConversationRetrievalProgressPhase.READ_STARTED,
                ConversationRetrievalProgressPhase.READ_COMPLETED,
            ],
        )
        generic_kinds = [event.kind for event in generic]
        for kind in (
            ContextExecutionEventKind.SEARCH_STARTED,
            ContextExecutionEventKind.SEARCH_COMPLETED,
            ContextExecutionEventKind.READ_STARTED,
            ContextExecutionEventKind.READ_COMPLETED,
        ):
            self.assertEqual(generic_kinds.count(kind), 1)
        self.assertIs(generic[0].kind, ContextExecutionEventKind.BOOTSTRAP)
        self.assertIs(generic[-1].kind, ContextExecutionEventKind.CHECKPOINT_ADVANCED)
        self.assertEqual(
            tuple(event.kind for event in self.services.event_store.read(session_id, selected)),
            (ConversationEventKind.USER_MESSAGE, ConversationEventKind.AGENT_REPLY),
        )

    async def test_prompt_and_draft_report_every_logical_agent_invocation(self) -> None:
        session_id = "live_context_debates"
        self.ready(session_id)

        for input_kind in ("prompt", "draft"):
            with self.subTest(input_kind=input_kind):
                debate_id = f"live_{input_kind}_debate"
                self.provider.active_debate_id = debate_id
                events: list[ContextExecutionEvent] = []
                initial_calls_before = len(self.provider.context_payloads)
                if input_kind == "prompt":
                    result = await self.runtime.run_prompt(
                        session_id,
                        "build one candidate",
                        debate_id=debate_id,
                        output_path=f"part-01/{debate_id}.md",
                        max_debate_rounds=1,
                        context_event_sink=events.append,
                    )
                else:
                    result = await self.runtime.run_draft(
                        session_id,
                        "revise this supplied draft",
                        debate_id=debate_id,
                        output_path=f"part-01/{debate_id}.md",
                        max_debate_rounds=1,
                        context_event_sink=events.append,
                    )
                self.assertIs(result.status, DebateStatus.CONSENSUS)
                initial_calls = len(self.provider.context_payloads) - initial_calls_before
                groups: dict[str, list[ContextExecutionEvent]] = {}
                for event in events:
                    groups.setdefault(event.operation_id, []).append(event)
                self.assertEqual(len(groups), initial_calls)
                self.assertGreater(len(groups), len(ROSTER))
                self.assertEqual({event.scope.agent_id for event in events}, set(ROSTER))
                self.assertTrue(all(event.phase is not None for event in events))
                self.assertTrue(all(event.round_number is not None for event in events))
                for operation_events in groups.values():
                    self.assertIn(
                        operation_events[0].kind,
                        {ContextExecutionEventKind.BOOTSTRAP, ContextExecutionEventKind.DELTA},
                    )
                    self.assertIs(
                        operation_events[-1].kind,
                        ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                    )
                    self.assertEqual(
                        len(
                            [
                                event
                                for event in operation_events
                                if event.kind
                                in {
                                    ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                                    ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                                }
                            ]
                        ),
                        1,
                    )
                self.assertIn(ContextExecutionEventKind.BOOTSTRAP, {event.kind for event in events})
                self.assertIn(ContextExecutionEventKind.DELTA, {event.kind for event in events})

    async def test_noncontextual_runtime_rejects_observer_before_any_effect(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        services = compose_filesystem_conversation_storage(workspace)

        class LegacyBoundAgent:
            def __init__(self, requests: list[object]) -> None:
                self.requests = requests

            async def converse(self, request: object) -> ConversationReply:
                self.requests.append(request)
                return ConversationReply(
                    request.session_id,  # type: ignore[attr-defined]
                    request.conversation_id,  # type: ignore[attr-defined]
                    request.turn_id,  # type: ignore[attr-defined]
                    "legacy reply",
                )

            async def invoke(self, request: object) -> object:
                raise AssertionError(request)

        class LegacyBackend(ScriptedBackend):
            def __init__(self) -> None:
                super().__init__(lambda request: AssertionError(request))
                self.conversation_requests: list[object] = []

            async def bind(self, definition: object) -> LegacyBoundAgent:
                self.definitions.append(definition)  # type: ignore[arg-type]
                return LegacyBoundAgent(self.conversation_requests)

        backend = LegacyBackend()
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_storage_services=services,
        )
        session_id = "noncontextual"
        conversation_id = services.catalog.create(session_id, "Compatibility").conversation_id
        with self.assertRaises(BackendProtocolError):
            await runtime.converse(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "must not persist",
                context_event_sink=lambda _event: None,
            )
        self.assertEqual(backend.conversation_requests, [])
        self.assertEqual(services.event_store.read(session_id, conversation_id), ())

        result = await runtime.converse(
            session_id,
            conversation_id,
            PLOT_ARCHITECT,
            "legacy behavior",
        )
        self.assertEqual(result.content, "legacy reply")
        self.assertEqual(len(backend.conversation_requests), 1)
        self.assertEqual(
            tuple(event.kind for event in services.event_store.read(session_id, conversation_id)),
            (ConversationEventKind.USER_MESSAGE, ConversationEventKind.AGENT_REPLY),
        )
