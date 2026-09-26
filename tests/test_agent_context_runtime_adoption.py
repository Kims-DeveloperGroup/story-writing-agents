"""End-to-end acceptance coverage for contextual story-runtime adoption."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any
from unittest import mock

from story_writing_agents.agent_context import (
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage,
    canonical_provider_json,
)
from story_writing_agents.agent_context import service as service_module
from story_writing_agents.agent_context.planning import (
    ContextCheckpointState,
    ContextDeliveryMode,
)
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderInvocation,
    ProviderResult,
    record_provider_failure_accounting,
)
from story_writing_agents.writing_artifacts import (
    ReferenceReadActionKind,
)


SearchSelection = tuple[tuple[str, ...], str] | tuple[str, tuple[str, ...], str]


def _framed_observation(value: object) -> dict[str, Any]:
    """Normalize one authoritative compact wire-v8 observation."""

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
        if not isinstance(raw, list) or len(raw) != 3 or raw[0] != "provenance":
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
                if len(record) == 8 and record[1] == "m" and record[6] == "candidate":
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
        self.invalid_read_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.correlation_mismatch_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.exhaust_selector: Callable[[dict[str, Any]], bool] = lambda _current: False
        self.search_only_selector: Callable[[dict[str, Any]], bool] = (
            lambda _current: False
        )
        self.block_selector: Callable[[dict[str, Any]], bool] = lambda _current: False
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
        artifact_path_prefix = None
        if len(selection) == 2:
            source_kinds, text = selection
        else:
            artifact_path_prefix, source_kinds, text = selection
        return cls._json(
            {
                "correlation": correlation,
                "action": {
                    "kind": "search",
                    "path_prefix": artifact_path_prefix,
                    "queries": [
                        {"text": text, "source_kinds": list(source_kinds)}
                    ],
                },
            }
        )

    def _debate_identity(
        self,
        binding: ProviderBinding,
        phase: str,
    ) -> tuple[str, str]:
        known = [*self.final_updates, *self.candidates]
        known.extend(debate_id for debate_id, _revision, _agent in self.conflicts)
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
            context_ids: list[object] = list(range(1, len(used_handles) + 1))
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
        action = {
            "correlation": correlation,
            "action": {
                "kind": "answer",
                "result": result,
                "used_handles": list(used_handles),
            },
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
            if self.correlation_mismatch_selector(current):
                response = self._json(
                    {
                        "correlation": "PRIVATE critique prose\x1b]unsafe\x07",
                        "action": {"kind": "read", "handles": []},
                    }
                )
            elif self.invalid_read_selector(current):
                response = self._json(
                    {
                        "correlation": correlation,
                        "action": {
                            "kind": "read",
                            "handles": ["conflict", "PRIVATE/critique text"],
                        },
                    }
                )
            elif search is None:
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
                    if isinstance(item, Mapping) and isinstance(item.get("handle"), str)
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
                            "correlation": correlation,
                            "action": {"kind": "read", "handles": list(handles)},
                        }
                    )
            elif "evidence" in frame:
                self.read_frames.append(frame)
                handles = tuple(
                    item["handle"]
                    for item in frame["evidence"]
                    if isinstance(item, Mapping) and isinstance(item.get("handle"), str)
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

    def test_interpreter_applies_full_pointer_splices_upserts_and_tombstones(
        self,
    ) -> None:
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
            with self.subTest(operation=operation), self.assertRaises(AssertionError):
                prepared().apply({"n": operation})
        with self.assertRaises(AssertionError):
            _CandidateWireInterpreter().apply(
                {"p": [["d", 1, [self.record(1, ["=", "k"])]]]}
            )

    def test_production_candidate_operation_bytes_and_full_fallbacks_are_exact(
        self,
    ) -> None:
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


if __name__ == "__main__":
    unittest.main()
