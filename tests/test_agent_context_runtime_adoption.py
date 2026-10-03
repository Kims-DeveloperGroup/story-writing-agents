"""Independent replay of provider-visible candidate references and text splices."""
from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace
from unittest import mock
from story_writing_agents.agent_context import canonical_provider_json
from story_writing_agents.agent_context import service as service_module
from story_writing_agents.agent_context.planning import ContextCheckpointState, ContextDeliveryMode
from story_writing_agents.provider_wire import add_contract_guidance, project_context_payload
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextProvenance
from story_writing_agents.agent_context.request_compaction import compact_context_payload


class _CandidateWireInterpreter:
    """Replay only named wire-v10 objects, checking every content identity."""
    def __init__(self):
        self.candidate = None
        self.records = {}

    @staticmethod
    def _digest(value):
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _facts(cls, raw):
        if type(raw) is not dict or set(raw) != {"source_reference_id", "record_reference_id", "content_digest", "content_utf8_bytes"}:
            raise AssertionError("candidate reference fields are malformed")
        source, record, digest, size = (raw[key] for key in ("source_reference_id", "record_reference_id", "content_digest", "content_utf8_bytes"))
        if type(source) is not int or source < 1 or type(record) is not int or record < 1 or type(digest) is not str or type(size) is not int or size < 0:
            raise AssertionError("candidate reference values are malformed")
        return (source, record), digest, size

    @classmethod
    def _verify(cls, text, raw):
        key, digest, size = cls._facts(raw)
        if cls._digest(text) != digest or len(text.encode("utf-8")) != size:
            raise AssertionError("candidate identity does not match reconstructed content")
        return key

    def _resolve_candidate(self, raw):
        if type(raw) is not dict:
            raise AssertionError("candidate delivery must be an object")
        delivery = raw.get("content_delivery")
        if delivery == "full_text":
            if set(raw) != {"content_delivery", "content_text"} or type(raw["content_text"]) is not str:
                raise AssertionError("full content is malformed")
            return raw["content_text"]
        required = {"content_delivery", "base_candidate_reference", "target_candidate_reference"}
        if delivery == "apply_text_splice":
            required.add("text_splice")
        elif delivery != "reuse_previous_candidate":
            raise AssertionError("unknown candidate delivery")
        if set(raw) != required:
            raise AssertionError("candidate delivery fields are malformed")
        key, _, _ = self._facts(raw["base_candidate_reference"])
        base = self.records.get(key)
        if base is None:
            raise AssertionError("base candidate record is unavailable")
        self._verify(base, raw["base_candidate_reference"])
        text = base
        if delivery == "apply_text_splice":
            splice = raw["text_splice"]
            if type(splice) is not dict or set(splice) != {"prefix_codepoint_count", "suffix_codepoint_count", "replacement_content_text"}:
                raise AssertionError("text splice fields are malformed")
            prefix, suffix, replacement = (splice[key] for key in ("prefix_codepoint_count", "suffix_codepoint_count", "replacement_content_text"))
            if type(prefix) is not int or prefix < 0 or type(suffix) is not int or suffix < 0 or prefix + suffix > len(base) or type(replacement) is not str:
                raise AssertionError("text splice bounds are malformed")
            text = base[:prefix] + replacement + (base[len(base) - suffix:] if suffix else "")
        self._verify(text, raw["target_candidate_reference"])
        return text

    def apply(self, payload):
        if "current_draft" in payload:
            self.candidate = self._resolve_candidate(payload["current_draft"])
        for group in payload.get("context_record_updates", []):
            if group["source_type"] != "current_consensus_run":
                raise AssertionError("unexpected source type")
            for record in group["records"]:
                key = group["source_reference_id"], record["record_reference_id"]
                if record["record_type"] == "consensus_message" and record["message_type"] == "candidate_text":
                    content = record["content_text"]
                    if content == {"content_reference": "/current_draft/content_text"}:
                        if payload.get("current_draft", {}).get("content_delivery") != "full_text":
                            raise AssertionError("full-text pointer requires literal candidate")
                        content = self.candidate
                    elif content == {"candidate_text_reference": "/current_draft"}:
                        content = self.candidate
                    if type(content) is not str:
                        raise AssertionError("candidate record must resolve to text")
                    self.records[key] = content
                else:
                    self.records.pop(key, None)
        for removal in payload.get("context_removals", []):
            source = removal["source_reference_id"]
            if removal["removal_type"] == "remove_record":
                self.records.pop((source, removal["record_reference_id"]), None)
            elif removal["removal_type"] in {"remove_source", "remove_source_content"}:
                self.records = {key: value for key, value in self.records.items() if key[0] != source}
            else:
                raise AssertionError("unknown removal type")
        return self.candidate


class CandidateWireContractTests(unittest.TestCase):
    """Prove named candidate operations reconstruct losslessly and fail closed."""
    @staticmethod
    def facts(source_alias, record_alias, content):
        return {"source_reference_id": source_alias, "record_reference_id": record_alias,
                "content_digest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content_utf8_bytes": len(content.encode("utf-8"))}

    @staticmethod
    def record(record_alias, content):
        return {"record_reference_id": record_alias, "record_type": "consensus_message", "sender": "execution_specialist",
                "consensus_phase": "draft_candidate", "consensus_round_number": 0, "candidate_revision_id": "r0",
                "message_type": "candidate_text", "content_text": content}

    @classmethod
    def operation(cls, base, target, *, base_alias, target_alias):
        prefix = 0
        while prefix < min(len(base), len(target)) and base[prefix] == target[prefix]:
            prefix += 1
        suffix = 0
        while suffix < min(len(base) - prefix, len(target) - prefix) and base[-suffix - 1] == target[-suffix - 1]:
            suffix += 1
        return {"content_delivery": "apply_text_splice", "base_candidate_reference": cls.facts(1, base_alias, base),
                "target_candidate_reference": cls.facts(1, target_alias, target), "text_splice": {
                    "prefix_codepoint_count": prefix,
                    "replacement_content_text": target[prefix:len(target) - suffix] if suffix else target[prefix:], "suffix_codepoint_count": suffix}}

    @staticmethod
    def payload(candidate=None, records=(), removals=()):
        result = {"context_record_updates": [{"source_type": "current_consensus_run", "source_reference_id": 1, "records": list(records)}], "context_removals": list(removals)}
        if candidate is not None:
            result["current_draft"] = {"content_delivery": "full_text", "content_text": candidate} if type(candidate) is str else candidate
        return result

    def test_interpreter_applies_full_pointer_splices_upserts_and_tombstones(self):
        interpreter = _CandidateWireInterpreter()
        reference = {"content_reference": "/current_draft/content_text"}
        first = ("A🙂\ue000\u0378\n" * 700) + "unsectioned tail"
        self.assertEqual(interpreter.apply(self.payload(first, [self.record(1, reference)])), first)
        self.assertEqual(interpreter.records[(1, 1)], first)
        reference = {"candidate_text_reference": "/current_draft"}
        pointer = {"content_delivery": "reuse_previous_candidate", "base_candidate_reference": self.facts(1, 1, first), "target_candidate_reference": self.facts(1, 2, first)}
        self.assertEqual(interpreter.apply(self.payload(pointer, [self.record(2, reference)])), first)
        self.assertEqual(interpreter.records[(1, 2)], first)
        second = first.replace("unsectioned tail", "reordered Δ tail")
        self.assertEqual(interpreter.apply(self.payload(self.operation(first, second, base_alias=2, target_alias=3), [self.record(3, reference)])), second)
        third = "prefix inserted\n" + second[:-20] + "deleted moved🙂"
        removal = {"removal_type": "remove_record", "source_reference_id": 1, "record_reference_id": 3}
        self.assertEqual(interpreter.apply(self.payload(self.operation(second, third, base_alias=3, target_alias=4), [self.record(3, "updated existing candidate"), self.record(4, reference)], [removal])), third)
        self.assertNotIn((1, 3), interpreter.records)
        self.assertEqual(interpreter.records[(1, 4)], third)
        self.assertEqual(interpreter.apply(self.payload()), third)
        self.assertEqual(interpreter.records[(1, 4)], third)
        interpreter.apply(self.payload(removals=[{"removal_type": "remove_source_content", "source_reference_id": 1}]))
        self.assertEqual(interpreter.records, {})

    def test_interpreter_rejects_stale_or_tampered_operations(self):
        base = "base🙂" * 500
        target = base + "changed"
        def prepared():
            current = _CandidateWireInterpreter()
            current.apply(self.payload(base, [self.record(1, {"content_reference": "/current_draft/content_text"})]))
            return current
        valid = self.operation(base, target, base_alias=1, target_alias=2)
        invalid = [
            {**valid, "extra": True}, {**valid, "content_delivery": "unknown"}, {**valid, "text_splice": {}},
            {**valid, "base_candidate_reference": self.facts(1, 99, base)},
            {**valid, "base_candidate_reference": {**self.facts(1, 1, base), "content_digest": "sha256:" + "0" * 64}},
            {**valid, "target_candidate_reference": {**self.facts(1, 2, target), "content_utf8_bytes": 1}},
            *({**valid, "text_splice": {**valid["text_splice"], key: value}} for key, value in (("prefix_codepoint_count", len(base) + 1), ("suffix_codepoint_count", len(base) + 1), ("replacement_content_text", 1))),
            {"op": "legacy_candidate_patch"},
        ]
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(AssertionError):
                prepared().apply(self.payload(operation))
        with self.assertRaises(AssertionError):
            _CandidateWireInterpreter().apply(self.payload(records=[self.record(1, {"content_reference": "/current_draft/content_text"})]))

    def test_production_candidate_operation_bytes_and_full_fallbacks_are_exact(self):
        def record(alias, content):
            encoded = content.encode("utf-8")
            return service_module._WireCandidateRecord("s1", alias, content, "sha256:" + hashlib.sha256(encoded).hexdigest(), len(encoded))
        projection = SimpleNamespace(mode=ContextDeliveryMode.DELTA, checkpoint_proposal=SimpleNamespace(sources=(), catalog_sources=()))
        checkpoint = SimpleNamespace(state=ContextCheckpointState.READY, provider_thread_id="thread-v10", sources=(), catalog_sources=())
        schema = {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False}
        def render_alternative(payload, paths):
            wire, paths, references = project_context_payload(payload, paths, ())
            wire = add_contract_guidance(wire, schema)
            wire, paths = compact_context_payload(wire, paths, references)
            return add_contract_guidance(wire, schema), paths, references
        def size(rendered):
            return len(canonical_provider_json(rendered[0]))
        def projected(candidate, projection=projection, checkpoint=checkpoint, duplicate=None, padding=None, diagnostics=False):
            raw = service_module._wire_candidate_value(candidate, projection, checkpoint, ())
            payload = {"q": ["draft", "a", "", [0, 0, 0, 4, 0, 8, 0, 200], ["draft", "text-v1"]], "n": raw}
            paths = {("q",): service_module.ContextComponentKind.CONTROL, ("n",): service_module.ContextComponentKind.CANDIDATE}
            if duplicate is not None:
                payload["p"] = [["d", 1, [[duplicate, "m", "plot_architect", "draft", 0, "r0", "candidate", ["=", "k"]]]]]
                paths[("p",)] = service_module.ContextComponentKind.DEBATE_MESSAGES
            if padding is not None:
                payload["i"], paths[("i",)] = padding, service_module.ContextComponentKind.CURRENT_INPUT
            encoded = candidate.encode("utf-8")
            entry = service_module.ContextManifestEntry("candidate", service_module.ContextSourceKind.RUNTIME_INPUT,
                service_module.ContextSemanticChannel.CANDIDATE, service_module.ContextPriority.P0_REQUIRED, 0, "r0",
                "sha256:" + hashlib.sha256(encoded).hexdigest(), ContextProvenance(ContextAuthorityKind.REQUEST, "candidate"), len(encoded), searchable=False)
            planned = SimpleNamespace(**vars(projection), units=(SimpleNamespace(entries=(entry,), body_utf8=encoded),))
            snapshot = canonical_provider_json(payload), dict(paths)
            selected = service_module._render_context_payload(payload, paths, (), schema=schema, projection=planned)
            full, compact = render_alternative({**payload, "n": candidate}, paths), render_alternative(payload, paths)
            self.assertEqual(selected, full if size(full) <= size(compact) else compact)
            self.assertEqual((canonical_provider_json(payload), paths), snapshot)
            self.assertEqual(selected[1][("current_draft",)], service_module.ContextComponentKind.CANDIDATE)
            self.assertEqual(selected[2], ())
            return (selected, full, compact, raw) if diagnostics else selected[0] if duplicate is not None else selected[0]["current_draft"]
        base = ("section🙂\n" * 800) + "tail A"
        targets = (base, base.replace("tail A", "tail B Δ"), "new lead\n" + base, base[:-30] + "arbitrary unsectioned splice")
        for index, target in enumerate(targets, start=2):
            with self.subTest(target=index):
                with mock.patch.object(service_module, "_latest_debate_candidate_record", side_effect=(record(1, base), record(index, target))):
                    combined = projected(target, duplicate=index)
                    actual = combined["current_draft"]
                expected = ({"content_delivery": "reuse_previous_candidate", "base_candidate_reference": self.facts(1, 1, base), "target_candidate_reference": self.facts(1, index, target)} if target == base else self.operation(base, target, base_alias=1, target_alias=index))
                self.assertEqual(actual, expected)
                self.assertEqual(canonical_provider_json(actual), canonical_provider_json(expected))
                interpreter = _CandidateWireInterpreter()
                interpreter.apply(self.payload(base, [self.record(1, {"content_reference": "/current_draft/content_text"})]))
                self.assertEqual(combined["context_record_updates"][0]["records"][0]["content_text"], {"candidate_text_reference": "/current_draft"})
                self.assertEqual(interpreter.apply(combined), target)
                self.assertEqual(interpreter.records[(1, index)], target)
                short_base = "x" * 1024
                short_target = (short_base, short_base[:-1] + "Δ", "new lead\n" + short_base, short_base[:-30] + "splice🙂")[index - 2]
                with mock.patch.object(service_module, "_latest_debate_candidate_record", side_effect=(record(1, short_base), record(index, short_target))):
                    selected, full, compact, raw = projected(short_target, duplicate=index, padding="", diagnostics=True)
                self.assertLess(len(canonical_provider_json(project_context_payload({"n": raw}, {})[0])), len(canonical_provider_json(project_context_payload({"n": short_target}, {})[0])))
                self.assertGreater(size(compact), size(full))
                self.assertEqual(selected, full)
                self.assertEqual(selected[0]["context_record_updates"][0]["records"][0]["content_text"], {"content_reference": "/current_draft/content_text"})
                if index == 2:
                    padding = "p" * (service_module.MAX_SESSION_CONTEXT_UTF8_BYTES - size(full))
                    with mock.patch.object(service_module, "_latest_debate_candidate_record", side_effect=(record(1, short_base), record(index, short_target))):
                        selected, full, compact, _ = projected(short_target, duplicate=index, padding=padding, diagnostics=True)
                    self.assertEqual(size(selected), service_module.MAX_SESSION_CONTEXT_UTF8_BYTES)
                    self.assertGreater(size(compact), service_module.MAX_SESSION_CONTEXT_UTF8_BYTES)
        with mock.patch.object(service_module, "_latest_debate_candidate_record", side_effect=(record(1, "small"), record(2, "small"))):
            self.assertEqual(projected("small"), {"content_delivery": "full_text", "content_text": "small"})
        with mock.patch.object(service_module, "_latest_debate_candidate_record", side_effect=(record(1, base), record(2, base + "stale"))):
            self.assertEqual(projected(base), {"content_delivery": "full_text", "content_text": base})
        for mode, state, thread in ((ContextDeliveryMode.BOOTSTRAP, ContextCheckpointState.READY, "thread"), (ContextDeliveryMode.DELTA, ContextCheckpointState.MISSING, None), (ContextDeliveryMode.DELTA, ContextCheckpointState.CORRUPT, None), (ContextDeliveryMode.DELTA, ContextCheckpointState.TAINTED, None), (ContextDeliveryMode.DELTA, ContextCheckpointState.STALE, None), (ContextDeliveryMode.DELTA, ContextCheckpointState.READY, None)):
            with self.subTest(mode=mode, state=state, thread=thread):
                self.assertEqual(projected(base, SimpleNamespace(mode=mode), SimpleNamespace(state=state, provider_thread_id=thread)), {"content_delivery": "full_text", "content_text": base})


if __name__ == "__main__":
    unittest.main()
