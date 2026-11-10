'Existing StructuredOutputValidationTests scenarios, moved without new cases.'
from __future__ import annotations
import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError, fields
from story_writing_agents.structured_output_validation import bounded_chunks, diagnose_json_text, carry_validation, diagnose_value, encoding_failure, failure_issues, validation_call, validation_failure
from tests.test_provider_schema_spec import check_schema


class StructuredOutputValidationTests(unittest.TestCase):
    def test_diagnose_json_text_reports_all_independent_safe_issues(self) -> None:
        schema = { "type": "object", "properties": {"message": {"type": "string", "minLength": 1},
            "count": {"type": "integer", "minimum": 1}, "maximum": {"type": "number", "maximum": 2},
            "short": {"type": "string", "maxLength": 2}, "few": {"type": "array", "minItems": 1},
            "many": {"type": "array", "maxItems": 1}, "mode": {"type": "string", "enum": ["draft", "review"]},
            "items": { "type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 0, },
            "required_note": {"type": "string"}, }, "required": ["message", "count", "items", "required_note"],
            "additionalProperties": False, }
        secret = "PRIVATE_PROVIDER_VALUE_한🙂"
        body = json.dumps({"message": "", "count": 0, "maximum": 3, "short": secret,
            "few": [], "many": [1, 2], "mode": secret, "items": ["", 3], secret: secret, }, ensure_ascii=False)
        issues = diagnose_json_text(body, schema)
        self.assertEqual(issues, ("$.required_note: required property is missing",
            "$: contains 1 unexpected properties (names withheld)", "$.message: expected at least 1 characters, got 0",
            "$.count: expected at least 1, got integer", "$.maximum: expected at most 2, got integer",
            f"$.short: expected at most 2 characters, got {len(secret)}",
            "$.few: expected at least 1 items, got 0", "$.many: expected at most 1 items, got 2",
            '$.mode: expected one of ["draft", "review"] (string), got string',
            "$.items[0]: expected at least 1 characters, got 0", "$.items[1]: expected string, got integer", ))
        self.assertNotIn(secret, repr(issues))

        # Independent character boundaries, not expected patterns copied from their producers.
        from story_writing_agents.lead_provider_contracts import (
            LeadDiscussionConstraints, _owned_visible_text_rule)
        from story_writing_agents import lead_exploration, lead_premise_decisions
        from story_writing_agents.structured_output_validation import SchemaRules
        values = ("", " ", "\t\n", "\x00", "x\x00", "x\n", "포기😀", "\ud800", "\udfff")
        for nonblank, accepted in (
            (False, (False, True, True, True, True, True, True, False, False)),
            (True, (False, False, False, False, False, True, True, False, False)),
        ):
            rules = SchemaRules.declare(_owned_visible_text_rule(
                {"type": "string"}, nonblank=nonblank))
            for value, expected in zip(values, accepted, strict=True):
                self.assertEqual(not rules.findings(value), expected, repr(value))
        for declaration in (lead_exploration._result_text_rule(),
                LeadDiscussionConstraints("control").schema()["properties"]["message"]):
            rules = SchemaRules.declare(declaration)
            for value, expected in zip(values[:-2],
                    (False, True, True, False, False, True, True), strict=True):
                self.assertEqual(not rules.findings(value), expected, repr(value))
        for value in (" ", "\n", "x\n", "포기😀"):
            self.assertIsNone(lead_exploration._text(value))
        for value in ("", "x\x00"):
            with self.assertRaises(ValueError): lead_exploration._text(value)
        with self.assertRaises(UnicodeEncodeError): lead_exploration._text("\ud800")
        analysis = lead_premise_decisions.premise_analysis_schema(("user_input",))
        identity = analysis["anyOf"][0]["properties"]["alternatives"]["items"][
            "properties"]["alternative_key"]
        rules = SchemaRules.declare(identity)
        for value, expected in (("A", True), ("0", True), ("a_b-9", True), ("a" * 64, True),
                ("_a", False), (".a", False), ("a.b", False), ("포기", False),
                ("a" * 65, False), ("a\n", False), ("a\r", False), ("a\x00", False)):
            self.assertEqual(not rules.findings(value), expected, repr(value))

        from story_writing_agents.structured_output_validation import compile_schema_pattern
        strict = r"^ok\z"
        rules = SchemaRules.declare({"type": "string", "pattern": strict})
        self.assertEqual(rules.schema_value()["pattern"], strict)
        self.assertFalse(rules.findings("ok"))
        self.assertTrue(rules.findings("ok\n"))
        self.assertIsNotNone(compile_schema_pattern(r"^ok$").search("ok\n"))
        for pattern, text in ((r"\\z", "\\z"), (r"[\\z]", "\\"), (r"[\\z]", "z"),
                              (r"^\(\?=literal\)\z", "(?=literal)"),
                              (r"[(?=]", "?"), (r"^(?:ok|yes)\z", "yes"),
                              (r"(?=ok)ok", "ok")):
            self.assertIsNotNone(compile_schema_pattern(pattern).fullmatch(text))
        custom = SchemaRules.declare(_owned_visible_text_rule(
            {"type": "string", "pattern": r"^A+$"}))
        self.assertFalse(custom.findings("A"))
        self.assertFalse(custom.findings("A\n"))
        self.assertTrue(custom.findings("B"))
        generic = SchemaRules.declare({"type": "string", "pattern": r"^A+$"})
        self.assertFalse(generic.findings("A"))
        self.assertTrue(generic.findings("B"))

        discussion = LeadDiscussionConstraints("control").schema()["properties"]["message"]
        self.assertEqual(discussion["pattern"], r"^[^\u0000]+\z")
        for nonblank, accepted in (
            (False, (False, True, True, False, False, True, True, False, False)),
            (True, (False, False, False, False, False, True, True, False, False)),
        ):
            rules = SchemaRules.declare(_owned_visible_text_rule(discussion, nonblank=nonblank))
            for value, expected in zip(values, accepted, strict=True):
                self.assertEqual(not rules.findings(value), expected, repr(value))

        def exported(pattern=r"^x\z"):
            return {"type": "object", "properties": {"slot_id": {"type": "string", "pattern": pattern}},
                    "required": ["slot_id"], "additionalProperties": False}
        for pattern in (r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\z", r"[\s\S]", r"[\\z]",
                        r"^(?:ok|yes)\z", r"^\(\?=literal\)\z", r"[-a-z_0-9]+",
                        "^[\x01-\ud7ff\ue000-\U0010ffff]+\\z"):
            self.assertEqual(check_schema(exported(pattern)), ())
        rejected_slot = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$(?![\s\S])"
        self.assertEqual(check_schema(exported(rejected_slot)),
                         (("$.properties.slot_id.pattern", "REGEX_LOOKAROUND"),))
        for pattern in (r"(?=x)x", r"(?!x)y", r"(?<=x)y", r"(?<!x)y", r"(x)\1",
                        r"\k<name>", r"(?P<name>x)", r"(?i)x", r"\p{Letter}", "[a", "(x",
                        r"[z-a]", r"x{2,1}", r"[a&&]", r"[a--b]", r"[a~~b]", r"[[:alpha:]]"):
            self.assertTrue(check_schema(exported(pattern)), pattern)
        literal = exported(".*")
        literal["properties"]["slot_id"].update(enum=["(?=literal)"], const="(?=literal)",
                                                 description="(?=literal)", title="pattern")
        self.assertEqual(check_schema(literal), ())
        literal["examples"] = [{"pattern": "(?=instance literal)"}]
        self.assertEqual(check_schema(literal), (("$.examples", "SCHEMA_UNREVIEWED_KEYWORD"),))
        reference = {"type": "object", "properties": {"allOf": {"$ref": "#/$defs/pattern"}},
                     "required": ["allOf"], "additionalProperties": False,
                     "$defs": {"pattern": {"type": "string", "pattern": r"^x\z"}}}
        self.assertEqual(check_schema(reference), ())
        reference["properties"]["allOf"]["$ref"] = "#/$defs/missing"
        self.assertIn(('$.properties.allOf["$ref"]', "LOCAL_SCHEMA_REFERENCE"), check_schema(reference))
        recursive = {"type": "object", "properties": {"next": {"anyOf": [{"$ref": "#"}, {"type": "null"}]}},
                     "required": ["next"], "additionalProperties": False}
        self.assertEqual(check_schema(recursive), ())
        for change, rule in (({"required": []}, "ALL_FIELDS_REQUIRED"),
                             ({"additionalProperties": True}, "CLOSED_OBJECT"),
                             ({"allOf": []}, "SCHEMA_UNREVIEWED_KEYWORD")):
            self.assertIn(rule, {r for _path, r in check_schema({**exported(), **change})})
        for field, value, rule in (("description", 3, "STRING_ANNOTATION"), ("title", {}, "STRING_ANNOTATION"),
                                   ("enum", [True], "PRIMITIVE_LITERAL_TYPE"),
                                   ("const", {}, "PRIMITIVE_LITERAL_TYPE"), ("type", {}, "SCHEMA_TYPE"),
                                   ("format", [], "STRING_FORMAT"), ("minLength", True, "NONNEGATIVE_INTEGER")):
            invalid = exported()
            invalid["properties"]["slot_id"][field] = value
            self.assertIn(rule, {r for _path, r in check_schema(invalid)})
        invalid = exported()
        invalid["properties"]["slot_id"].update(minLength=2, maxLength=1)
        self.assertIn("SCHEMA_RANGE", {r for _path, r in check_schema(invalid)})
        numeric = {"type": "object", "properties": {"n": {"type": "integer", "enum": [True]}},
                   "required": ["n"], "additionalProperties": False}
        self.assertIn("PRIMITIVE_LITERAL_TYPE", {r for _path, r in check_schema(numeric)})
        numeric["properties"]["n"] = {"type": "number", "exclusiveMinimum": 1, "maximum": 1}
        self.assertIn("SCHEMA_RANGE", {r for _path, r in check_schema(numeric)})
        limits = exported(".*")
        limits["properties"]["slot_id"]["enum"] = [str(i) for i in range(1000)]
        self.assertEqual(check_schema(limits), ())
        limits["properties"]["slot_id"]["enum"].append("1000")
        self.assertIn(("$", "ENUM_LIMIT"), check_schema(limits))
        limits["properties"]["slot_id"]["enum"] = [str(i).zfill(3) + "a" * 60 for i in range(250)]
        self.assertEqual(check_schema(limits), ())
        limits["properties"]["slot_id"]["enum"].append("250" + "a" * 60)
        self.assertIn("LARGE_ENUM_STRING_LIMIT", {r for _path, r in check_schema(limits)})
        limits = exported(".*")
        limits["properties"]["slot_id"]["const"] = "a" * 119993
        self.assertEqual(check_schema(limits), ())
        limits["properties"]["slot_id"]["const"] += "a"
        self.assertIn(("$", "SCHEMA_STRING_LIMIT"), check_schema(limits))
        limits = {"type": "object", "properties": {str(i): {"type": "string"} for i in range(5000)},
                  "required": [str(i) for i in range(5000)], "additionalProperties": False}
        self.assertEqual(check_schema(limits), ())
        limits["properties"]["extra"] = {"type": "string"}
        limits["required"].append("extra")
        self.assertIn(("$", "PROPERTY_LIMIT"), check_schema(limits))
        nested = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        for _ in range(9):
            nested = {"type": "object", "properties": {"child": nested}, "required": ["child"], "additionalProperties": False}
        self.assertEqual(check_schema(nested), ())
        nested = {"type": "object", "properties": {"child": nested}, "required": ["child"], "additionalProperties": False}
        self.assertIn("INLINE_NESTING_LIMIT", {r for _path, r in check_schema(nested)})
        from contextlib import redirect_stdout
        from pathlib import Path
        from tests.test_provider_schema_spec import main
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema.json"
            for raw, code in ((json.dumps(exported()), 0), (json.dumps(exported(rejected_slot)), 1),
                              ('{"type":"object","type":"string"}', 2), ('{"x":NaN}', 2),
                              ('{"x":Infinity}', 2), ('{"x":-Infinity}', 2), ('{', 2)):
                path.write_text(raw, encoding="utf-8")
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main([str(path)]), code)
                self.assertIs(json.loads(output.getvalue())["native_admitted"], False)

    def test_union_selects_matching_branch_without_other_branch_noise(self) -> None:
        schema = {"anyOf": [{"type": "object", "properties": { "kind": {"type": "string", "enum": ["search"]},
            "query": {"type": "string"}, }, "required": ["kind", "query"], "additionalProperties": False},
            {"type": "object", "properties": {"kind": {"type": "string", "enum": ["answer"]},
            "message": {"type": "string", "minLength": 1}, "citation_ids": {"type": "array", "minItems": 0}, },
            "required": ["kind", "message", "citation_ids"], "additionalProperties": False}, ]}
        issues = diagnose_json_text( '{"kind":"answer","message":0,"PRIVATE_UNKNOWN":"secret"}', schema, )
        self.assertEqual(issues, ("$.citation_ids: required property is missing",
            "$: contains 1 unexpected properties (names withheld)", "$.message: expected string, got integer", ))
        self.assertNotIn("query", repr(issues)); self.assertNotIn("PRIVATE_UNKNOWN", repr(issues))

        # Literal diagnostic oracles remain independent of evaluation reuse.
        from collections.abc import Mapping; from decimal import Decimal
        text_rule = {"type": "string", "enum": ["ok"]}
        references = {"type": "object", "properties": { "left": {"$ref": "#/$defs/text"},
            "right": {"$ref": "#/$defs/text"}}, "required": ["left", "right"], "$defs": {"text": text_rule}}
        supplied = {"left": "ok", "right": "ok"}; self.assertEqual(diagnose_value(supplied, references), ())
        supplied["right"] = 7
        self.assertEqual(diagnose_value(supplied, references), ("$.right: expected string, got integer",))
        supplied["left"] = None
        self.assertEqual(diagnose_value(supplied, references),
                         ("$.left: expected string, got null", "$.right: expected string, got integer"))
        supplied.update(left="ok", right="ok"); text_rule["enum"] = ["changed"]
        self.assertEqual(diagnose_value(supplied, references), (
            '$.left: expected one of ["changed"] (string), got string',
            '$.right: expected one of ["changed"] (string), got string'))
        text_rule["enum"] = ["ok"]; self.assertEqual(diagnose_value(supplied, references), ())
        alternatives = {"anyOf": [{"type": "integer"}, {"type": "string", "minLength": 2}]}
        for value in (3, "ok"): self.assertEqual(diagnose_value(value, alternatives), ())
        self.assertEqual(diagnose_value("q", alternatives), ("$: expected at least 2 characters, got 1",))
        self.assertEqual(diagnose_value([], alternatives), (
            "$: allowed alternative 1 requires expected integer, got array",
            "$: allowed alternative 2 requires expected string, got array"))
        self.assertEqual(diagnose_value(1, {"anyOf": [{"type": "integer"}, {"type": "number"}]}),
                         ("$: expected one unambiguous schema alternative",))
        def object_alternative(*names):
            return {"type": "object", "properties": {name: {"type": "integer"} for name in names},
                    "required": list(names)}
        self.assertEqual(diagnose_value({}, {"anyOf": [object_alternative("a"), object_alternative("a", "b")]}),
                         ("$.a: required property is missing",))
        self.assertEqual(diagnose_value({}, {"anyOf": [
            object_alternative("z", "a", "left"), object_alternative("z", "a", "right")]}),
            ("$.z: required property is missing", "$.a: required property is missing"))
        repeated_ref = {"$ref": "#/$defs/shared"}
        repeated_rule = {"anyOf": [repeated_ref, repeated_ref], "$defs": {"shared": {"type": "integer"}}}
        self.assertEqual(diagnose_value(1, repeated_rule), ("$: expected one unambiguous schema alternative",))
        self.assertEqual(diagnose_value("wrong", repeated_rule), ("$: expected integer, got string",))
        from story_writing_agents import structured_output_validation as diagnostics
        import sys
        original_match, matched = diagnostics._matches_type, []
        def observed_match(frame, event, _arg):
            if event == "call" and frame.f_code is original_match.__code__:
                matched.append((frame.f_locals["value"], frame.f_locals["expected"]))
        previous_match_profile = sys.getprofile()
        try:
            sys.setprofile(observed_match)
            self.assertEqual(diagnose_value(1, repeated_rule), ("$: expected one unambiguous schema alternative",))
        finally: sys.setprofile(previous_match_profile)
        self.assertEqual(diagnose_value(1, repeated_rule), ("$: expected one unambiguous schema alternative",))
        self.assertEqual(matched, [(1, "integer")])
        repeated_rule["anyOf"] = [object_alternative("shared", name) for name in ("left", "right")]
        for alternative in repeated_rule["anyOf"]: alternative["properties"]["shared"] = repeated_ref
        self.assertEqual(diagnose_value({"shared": 1}, repeated_rule), (
            "$.left: allowed alternative 1 requires required property is missing",
            "$.right: allowed alternative 2 requires required property is missing"))
        nullable = {"type": ["integer", "null"], "enum": [1, None]}
        for value in (1, Decimal("1"), None): self.assertEqual(diagnose_value(value, nullable), ())
        class IntegerSubclass(int): pass
        for value, finding in ((True, "$: expected integer or null, got boolean"),
                (IntegerSubclass(1), "$: expected integer or null, got non-JSON value"),
                (Decimal("NaN"), "$: expected a finite number, got number")):
            self.assertEqual(diagnose_value(value, nullable), (finding,))
        events, trace_options = [], {"mutate": False, "error": None}
        class TracedSchema(Mapping):
            def __iter__(self): return iter(text_rule)
            def __len__(self): return len(text_rule)
            def __getitem__(self, key):
                events.append(key)
                if key == "enum" and trace_options["error"] is not None: raise trace_options["error"]
                if key == "maxLength" and trace_options["mutate"]:
                    text_rule["enum"] = ["changed"]; trace_options["mutate"] = False
                return text_rule[key]
        references["$defs"]["text"] = TracedSchema()
        accesses = ["$ref", "anyOf", "type", "enum", "pattern", "minLength", "maxLength"]
        self.assertEqual(diagnose_value(supplied, references), ())
        self.assertEqual(events, accesses * 2); events.clear(); trace_options["mutate"] = True
        self.assertEqual(diagnose_value(supplied, references),
                         ('$.right: expected one of ["changed"] (string), got string',))
        self.assertEqual(events, accesses * 2)
        text_rule["enum"] = ["ok"]; trace_options["error"] = RuntimeError("fixture mapping interruption")
        events.clear()
        with self.assertRaises(RuntimeError) as interrupted: diagnose_value(supplied, references)
        self.assertIs(interrupted.exception, trace_options["error"])
        self.assertEqual(events, accesses[:4]); trace_options["error"] = None; events.clear()
        self.assertEqual(diagnose_value(supplied, references), ()); self.assertEqual(events, accesses * 2)
        with self.assertRaisesRegex(ValueError, "^schema rejected$") as absent_definition:
            diagnose_value("ok", {"$ref": "#/$defs/absent"})
        self.assertEqual(failure_issues(absent_definition.exception), ("$: local definition missing",))
        # End independent diagnostic oracles.
        from story_writing_agents.structured_output_validation import (
            SchemaRules, schema_rule_fingerprint as single, schema_rule_fingerprints as plural)
        from story_writing_agents.lead_provider_contracts import _compile_closed_choices; import sys
        leaf = {"type": "string", "enum": ["draft"]}
        graph = {"type": "integer", "enum": [1], "$defs": {"choice": leaf}}
        draft_id = hashlib.sha256(b'{"enum":["draft"],"type":"string"}').hexdigest()
        integer_id = hashlib.sha256(b'{"enum":[1],"type":"integer"}').hexdigest()
        for selected, expected in ((None, integer_id), (leaf, draft_id),
                                   ({"$ref": "#/$defs/choice"}, draft_id)):
            self.assertEqual(single(graph, selected=selected), expected)
            self.assertEqual(plural(graph, selected=selected), (expected, {"choice": draft_id}))
        leaf["enum"][0] = "review"; self.assertNotEqual(single(graph, selected=leaf), draft_id)
        leaf["enum"][0] = "draft"; self.assertEqual(single(graph, selected=leaf), draft_id)
        graph["$defs"]["unused"] = {"$ref": "#/$defs/missing"}
        self.assertEqual(single(graph, selected=leaf), draft_id)
        for operation in (plural, SchemaRules.declare, _compile_closed_choices):
            with self.assertRaisesRegex(ValueError, "^schema rejected$") as rejected: operation(graph)
            self.assertEqual(failure_issues(rejected.exception), ("$: local definition missing",))
        events = []
        class Observed(dict):
            def __deepcopy__(self, memo): return self
            def get(self, key, *default):
                if key == "$defs": events.append(("definitions", id(self)))
                return super().get(key, *default)
            def items(self):
                frame = sys._getframe(1)
                while frame and frame.f_code is not plural.__code__: frame = frame.f_back
                events.append(("items", id(self), frame is not None and frame.f_locals["selected"] is not None))
                del frame
                return super().items()
        selected = Observed(leaf); first, last = Observed(type="boolean"), Observed(type="null")
        observed = Observed(type="integer", **{"$defs": {"z": last, "a": first}})
        self.assertEqual(single(observed, selected=selected), draft_id)
        self.assertEqual(events, [("items", id(selected), False)]); events.clear()
        self.assertEqual(plural(observed, selected=selected)[0], draft_id)
        self.assertEqual(events, [("items", id(selected), True), ("definitions", id(observed)),
                                  ("items", id(first), True), ("items", id(last), True)])
        choices = {"anyOf": [ {"type": "object", "properties": {"kind": {"type": "string", "enum": [kind]},
             "payload": {"type": "string"}}, "required": ["kind", "payload"], "additionalProperties": False}
            for kind in ("a", "b")], "$defs": {"unused": first}}
        events.clear(); compiled = _compile_closed_choices(choices)
        self.assertNotIn("anyOf", compiled); self.assertIn(("items", id(first), True), events)
        before, after = SchemaRules.declare(choices), SchemaRules.declare(compiled)
        for value, valid in (({"kind": "a", "payload": "value"}, True), ({"kind": "b", "payload": "value"}, True),
                             ({"kind": "c", "payload": "value"}, False), ({"kind": "a", "payload": 0}, False)):
            self.assertEqual(not before.findings(value), valid); self.assertEqual(not after.findings(value), valid)
        from story_writing_agents.structured_output_validation import bound_schema_node_fingerprint as bound_fingerprint
        query_graph = SchemaRules.declare({"type": "object", "properties": {"kind": leaf},
            "required": ["kind"], "additionalProperties": False, "$defs": {"unused": {"type": "null"}}})
        query_source = query_graph.canonical_bytes
        query_method, query_slot = SchemaRules.node_fingerprint, SchemaRules.canonical_bytes
        query_code, query_defaults = query_method.__code__, query_method.__defaults__; query_calls, slot_reads = [], []
        def query_profile(frame, event, _arg):
            if event == "call" and frame.f_code is plural.__code__: query_calls.append("plural")
            if event == "call" and frame.f_code is SchemaRules.node_fingerprint.__code__: query_calls.append("public")
        previous_profile = sys.getprofile()
        try:
            sys.setprofile(query_profile); value, proof = bound_fingerprint(query_graph, ("kind",))
            self.assertEqual(value, draft_id); self.assertIsNotNone(proof); self.assertEqual(query_calls, ["plural"])
            for _ in range(2):
                query_calls.clear(); value, reused = bound_fingerprint(query_graph, ("kind",), proof=proof)
                self.assertEqual(value, draft_id); self.assertIs(reused, proof); self.assertEqual(query_calls, [])
            query_calls.clear()
            self.assertEqual(bound_fingerprint(query_graph, ("kind",), proof=None), (draft_id, None))
            self.assertEqual(query_calls, ["public", "plural"])
            equivalent_source = bytes(bytearray(query_source)); self.assertIsNot(equivalent_source, query_source)
            changed_source = json.loads(query_source); changed_source["properties"]["kind"]["enum"] = ["review"]
            review_id = hashlib.sha256(b'{"enum":["review"],"type":"string"}').hexdigest()
            for source, expected in ((equivalent_source, draft_id),
                    (json.dumps(changed_source, sort_keys=True, separators=(",", ":")).encode(), review_id)):
                object.__setattr__(query_graph, "canonical_bytes", source)
                self.assertEqual(query_graph.node_fingerprint(("kind",)), expected); query_calls.clear()
                self.assertEqual(bound_fingerprint(query_graph, ("kind",), proof=proof), (expected, None))
                self.assertEqual(query_calls, ["public", "plural"])
            malformed_source = json.loads(query_source)
            malformed_source["$defs"]["unused"] = {"$ref": "#/$defs/missing"}
            object.__setattr__(query_graph, "canonical_bytes", json.dumps(malformed_source).encode())
            with self.assertRaisesRegex(ValueError, "^schema rejected$") as original_failure:
                query_graph.node_fingerprint(("kind",))
            self.assertEqual(failure_issues(original_failure.exception), ("$: local definition missing",))
            for options, expected_calls in (({}, ["plural"]), ({"proof": proof}, ["public", "plural"])):
                query_calls.clear()
                with self.assertRaisesRegex(ValueError, "^schema rejected$") as proof_failure:
                    bound_fingerprint(query_graph, ("kind",), **options)
                self.assertEqual(failure_issues(proof_failure.exception), failure_issues(original_failure.exception))
                self.assertEqual(query_calls, expected_calls)
            object.__setattr__(query_graph, "canonical_bytes", query_source)
            for field, original in (("__code__", query_code), ("__defaults__", query_defaults)):
                replacement = original.replace() if field == "__code__" else tuple(list(original))
                self.assertIsNot(replacement, original); setattr(query_method, field, replacement); query_calls.clear()
                self.assertEqual(bound_fingerprint(query_graph, ("kind",), proof=proof), (draft_id, None))
                self.assertEqual(query_calls, ["public", "plural"]); setattr(query_method, field, original)
                query_calls.clear(); value, reused = bound_fingerprint(query_graph, ("kind",), proof=proof)
                self.assertEqual(value, draft_id); self.assertIs(reused, proof); self.assertEqual(query_calls, [])
            query_state = [draft_id]
            def live_query(_self, _path=()): return query_state[0]
            SchemaRules.node_fingerprint = live_query
            for expected in (draft_id, review_id):
                query_state[0] = expected; query_calls.clear()
                self.assertEqual(bound_fingerprint(query_graph, ("kind",), proof=proof), (expected, None))
                self.assertEqual(query_calls, ["public"])
            SchemaRules.node_fingerprint = query_method
            def live_source(value): slot_reads.append(value); return query_slot.__get__(value, type(value))
            SchemaRules.canonical_bytes = property(live_source); query_calls.clear()
            self.assertEqual(bound_fingerprint(query_graph, ("kind",), proof=proof), (draft_id, None))
            self.assertEqual(query_calls, ["public", "plural"]); self.assertEqual(slot_reads, [query_graph])
            SchemaRules.canonical_bytes = query_slot
            root_fingerprint = query_graph.node_fingerprint(); query_calls.clear()
            self.assertEqual(bound_fingerprint(query_graph, proof=proof), (root_fingerprint, None))
            self.assertEqual(query_calls, ["public", "plural"])
            original_record = proof._record; replacement_record = tuple(list(original_record))
            self.assertIsNot(replacement_record, original_record)
            try:
                object.__setattr__(proof, "_record", replacement_record); query_calls.clear()
                with self.assertRaisesRegex(ValueError, "^frozen schema node fingerprint proof changed$"):
                    bound_fingerprint(query_graph, ("kind",), proof=proof)
                self.assertEqual(query_calls, [])
            finally: object.__setattr__(proof, "_record", original_record)
            query_calls.clear(); value, reused = bound_fingerprint(query_graph, ("kind",), proof=proof)
            self.assertEqual(value, draft_id); self.assertIs(reused, proof); self.assertEqual(query_calls, [])
        finally:
            sys.setprofile(previous_profile); SchemaRules.node_fingerprint = query_method
            query_method.__code__, query_method.__defaults__ = query_code, query_defaults
            SchemaRules.canonical_bytes = query_slot; object.__setattr__(query_graph, "canonical_bytes", query_source)

        from story_writing_agents.lead_provider_contracts import factor_rule_schema
        encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         allow_nan=False, separators=(',', ':')).encode('utf-8')
        trial_code = next(code for code in factor_rule_schema.__code__.co_consts
                          if isinstance(code, type(factor_rule_schema.__code__)) and code.co_name == 'trial_size')
        costs = []
        def observe_cost(frame, event, returned):
            if event == 'return' and frame.f_code is trial_code:
                source = frame.f_back.f_locals; before = tuple(source['shared'].items())
                original, aliases = source['assemble']()
                costs.append((returned[0], dict(returned[1]), len(encode(original)), aliases,
                              before == tuple(source['shared'].items())))
        def factored(value):
            first_cost = len(costs); previous = sys.getprofile(); sys.setprofile(observe_cost)
            try: result = factor_rule_schema(value)
            finally: sys.setprofile(previous)
            for actual, aliases, original, original_aliases, untouched in costs[first_cost:]:
                self.assertEqual(actual, original); self.assertEqual(aliases, original_aliases)
                self.assertTrue(untouched)
            return result
        def pair(leaf): return {'type': 'object', 'properties': {'left': leaf, 'right': leaf},
                    'required': ['left', 'right'], 'additionalProperties': False}
        def shared_pair(leaf): return {**pair({'$ref': '#/$defs/a'}), '$defs': {'a': leaf}}
        for width, inline_size, shared_size, wins in ((25, 214, 215, False), (26, 216, 216, False),
                                                     (27, 218, 217, True), (80, 324, 270, True)):
            leaf = {'type': 'string', 'enum': ['x' * width]}; original, shared = pair(leaf), shared_pair(leaf)
            self.assertEqual(len(encode(original)), inline_size); self.assertEqual(len(encode(shared)), shared_size)
            first_cost = len(costs); self.assertEqual(factored(original), shared if wins else original)
            self.assertEqual(len(costs) - first_cost, 1); self.assertEqual(costs[-1][0], shared_size)
        leaf = {'type': 'string', 'enum': ['x' * 51]}; nested = leaf; supplied = 'x' * 51
        for _ in range(64): nested = {'type': 'array', 'items': nested}; supplied = [supplied]
        original = pair(nested); first_cost = len(costs); actual = factored(original)
        self.assertEqual(len(encode(original)), 3466); self.assertEqual(costs[first_cost][0], 3467)
        self.assertLess(len(encode(actual)), len(encode(original)))
        for rules in (SchemaRules.declare(original), SchemaRules.declare(actual)):
            self.assertFalse(rules.findings({'left': supplied, 'right': supplied}))
            self.assertTrue(rules.findings({'left': supplied}))
        for leaf, supplied in (({'type': 'string', 'enum': ['한🙂' * 4]}, '한🙂' * 4), ({'type': 'string',
            'enum': ['"\\\n' * 5]}, '"\\\n' * 5), ({'type': 'number', 'enum': [-0.0, 1e-6, 1e20]}, -0.0),
            ({'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, {}),
            ({'type': 'array', 'items': {'type': 'null'}, 'minItems': 0, 'maxItems': 0}, [])):
            original, shared = pair(leaf), shared_pair(leaf)
            expected = shared if len(encode(shared)) < len(encode(original)) else original
            actual = factored(original); self.assertEqual(encode(actual), encode(expected))
            for rules in (SchemaRules.declare(original), SchemaRules.declare(actual)):
                self.assertFalse(rules.findings({'left': supplied, 'right': supplied}))
                self.assertTrue(rules.findings({'left': supplied}))
        body = {'type': 'string', 'enum': ['x' * 90]}
        descriptions = {'left': '{"$ref":"#/$defs/quoted"}', 'right': '한🙂 "quoted" \\ newline\n'}
        original = {**pair(body), 'properties': {name: {**body, 'description': text}
                                               for name, text in descriptions.items()}}
        expected = {**original, 'properties': {name: {'$ref': '#/$defs/a', 'description': text}
                                              for name, text in descriptions.items()}, '$defs': {'a': body}}
        self.assertEqual(factored(original), expected); names = [f'quoted"한\\{number}' for number in range(53)]
        definitions = {name: {'type': 'integer', 'enum': [number]} for number, name in enumerate(names)}
        properties = {f'p{number}': {'$ref': '#/$defs/' + names[number]} for number in range(52)}
        properties['again'] = {'$ref': '#/$defs/' + names[0]}
        original = {'type': 'object', 'properties': properties, 'required': list(properties),
                    'additionalProperties': False, '$defs': definitions, 'description': '#/$defs/' + names[0]}
        ordered = sorted(names, key=lambda name: hashlib.sha256(encode(definitions[name])).hexdigest())
        internal = {name: f'd{number}' for number, name in enumerate(ordered)}
        counts = {name: 0 if number == 52 else 2 if number == 0 else 1 for number, name in enumerate(names)}
        alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        aliases = dict(zip(sorted(names, key=lambda name: (-counts[name], internal[name])), (*alphabet, 'aa')))
        expected = {**original, 'properties': {name: {'$ref': '#/$defs/' + aliases[ref['$ref'][8:]]} for name,
            ref in properties.items()}, '$defs': {aliases[name]: value for name, value in definitions.items()}}
        actual = factored(original); self.assertEqual(actual, expected)
        self.assertEqual(actual['$defs']['a'], definitions[names[0]])
        self.assertEqual(actual['$defs']['aa'], definitions[names[52]]); from collections.abc import Mapping
        class Source(Mapping):
            def __iter__(self): events.append('iterate'); return iter(original)
            def __len__(self): events.append('length'); return len(original)
            def __getitem__(self, name): events.append(('read', name)); return original[name]
        events.clear(); dict(Source()); access_order = list(events); events.clear()
        self.assertEqual(factored(Source()), actual); self.assertEqual(events, access_order)
        for invalid, issue in (({'$ref': '#/$defs/missing'}, '$: local definition missing'),
                               ({'$ref': '#/$defs/unused'}, '$defs: cyclic local definition')):
            malformed = {**original, '$defs': {**definitions, 'unused': invalid}}
            for operation in (SchemaRules.declare, factored):
                cycle = operation is factored and invalid['$ref'] == '#/$defs/unused'
                message = 'cyclic schema rule graph' if cycle else 'schema rejected'
                with self.assertRaisesRegex(ValueError, f'^{message}$') as caught: operation(malformed)
                self.assertEqual(failure_issues(caught.exception), () if cycle else (issue,))

        # Admission is separate from validation; zero remaining work is permitted.
        plain = diagnostics._diagnostic_plain
        self.assertTrue(plain([None, True, 1, 1.25, "한\n"], {}))
        scalar_mixed = [0, "resume", {"nested": [None, 1.25]}, True, "start"]
        self.assertTrue(plain(scalar_mixed, {})); self.assertTrue(plain(None, {"metadata": scalar_mixed}))
        class DictSubclass(dict): pass
        class ListSubclass(list): pass
        class FloatSubclass(float): pass
        events.clear()
        self.assertTrue(plain(Decimal(1), {})); self.assertTrue(plain([Decimal("1.25"), {"n": Decimal(2)}], {}))
        self.assertFalse(plain(None, {"metadata": Decimal(1)}))
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), IntegerSubclass(1), DictSubclass(), ListSubclass(), FloatSubclass(1),
                      TracedSchema(), float("nan"), float("inf"), float("-inf")):
            self.assertFalse(plain(value, {})); self.assertFalse(plain(None, {"metadata": value}))
            for late_scalar in ([value, {}, None, 1.25], [{"nested": value}, None, 1.25]):
                self.assertFalse(plain(late_scalar, {})); self.assertFalse(plain(None, {"metadata": late_scalar}))
        self.assertEqual(events, [])
        key_events = []
        class ForeignKey(str):
            __hash__ = str.__hash__
            def __eq__(self, other): key_events.append(other); raise RuntimeError("fixture admission key lookup")
        for root in ({ForeignKey("$defs"): {}}, {"metadata": {ForeignKey("$ref"): "x"}},
                     {"$defs": {ForeignKey("name"): {}}}): self.assertFalse(plain(None, root))
        self.assertEqual(key_events, [])
        literal = {"$ref": "not-a-schema-ref"}; shared = {}
        self.assertTrue(plain(literal, {})); self.assertFalse(plain(None, literal))
        self.assertTrue(plain([shared, shared], {}))
        cyclic_list = []; cyclic_list.append(cyclic_list)
        cyclic_dict = {}; cyclic_dict["self"] = cyclic_dict
        for value in (cyclic_list, cyclic_dict):
            self.assertFalse(plain(value, {})); self.assertFalse(plain(None, {"metadata": value}))
        self.assertFalse(plain(None, {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"}))
        self.assertTrue(plain(None, {"$defs": {"name": {}}, "$ref": "#/$defs/name"}))
        for reference in ("", "#/$defs/", "#/$defs/missing", "external", "#/$defs/a/b", "#/$defs/a~1b", 1):
            self.assertFalse(plain(None, {"$defs": {"name": {}}, "$ref": reference}))
        self.assertFalse(plain(None, {"$defs": {"name": []}, "$ref": "#/$defs/name"}))
        self.assertFalse(plain(None, {"$defs": []}))
        # Root + data-list + scalars charges N+2; shared revisits instead isolate pending N+2.
        for length, expected in ((262_142, True), (262_143, False)):
            self.assertIs(plain([0] * length, {}), expected)
            self.assertIs(plain([{}] * length, {}), expected)
        # One shared list visited in both roles charges 2N+3, not N+2.
        for length, expected in ((131_070, True), (131_071, False)):
            shared = [0] * length
            self.assertIs(plain(shared, {"metadata": shared}), expected)
        spend, repeat = [0] * 131_071, [{}] * 131_071
        self.assertTrue(plain({"spend": spend, "repeat": repeat}, {}))
        self.assertFalse(plain({"repeat": repeat, "spend": spend}, {}))
        self.assertTrue(plain(None, {"$defs": {"t": {"repeat": repeat}},
                                    "$ref": "#/$defs/t", "spend": spend}))

        # Public H-compatible oracle: admission must not speculate through metaclass equality.
        metaclass_events, metaclass_observations = [], []
        safe_hash = next(number for number in range(4) if number not in {hash(int), hash(float), hash(Decimal)})
        for raise_equality in (False, True):
            equality_failure = RuntimeError("fixture metaclass equality")
            class ForeignMeta(type):
                def __hash__(cls): return safe_hash
                def __eq__(cls, other):
                    metaclass_events.append(other)
                    if raise_equality: raise equality_failure
                    return True
            foreign = ForeignMeta("Foreign", (), {})()
            try: result = diagnose_value(foreign, {"type": "string"})
            except RuntimeError as failure: result = failure
            metaclass_observations.append((result, tuple(metaclass_events))); metaclass_events.clear()
        self.assertEqual(metaclass_observations, [
            (("$: expected string, got non-JSON value",), ()), (("$: expected string, got non-JSON value",), ())])
        class CollisionMeta(ForeignMeta):
            def __hash__(cls): return hash(float)
        with self.assertRaises(RuntimeError) as collision:
            diagnose_value(CollisionMeta("Collision", (), {})(), {"type": "string"})
        self.assertIs(collision.exception, equality_failure); self.assertTrue(metaclass_events)
        metaclass_events.clear()
        # This direct private assertion is J/candidate-only; the H public probe excludes it.
        self.assertFalse(plain(ForeignMeta("FreshForeign", (), {})(), {}))
        self.assertEqual(metaclass_events, [])

        # Public original-source oracle: unsafe numeric equality must remain a fresh boundary.
        from collections.abc import Mapping
        from story_writing_agents.structured_output_validation import TypedDecoder
        hook_observations = []
        for numeric_base in (int, float):
            hook_flags, hook_events, hook_bodies = [False, False], [], []
            equality_failure = RuntimeError("fixture hook default equality")
            class NumericMeta(type):
                def __hash__(cls): return hash(numeric_base)
                def __eq__(cls, other):
                    if hook_flags[0]:
                        hook_events.append("numeric_exception"); raise equality_failure
                    return other is numeric_base
            class NumericValue(numeric_base, metaclass=NumericMeta): pass
            numeric = NumericValue(7)
            def direct(value, captured=numeric):
                hook_bodies.append("direct"); return value
            sealed = TypedDecoder("fixture.numeric.default", "1", direct)
            self.assertIsNone(sealed.require_provenance()); hook_flags[0] = True; observed = None
            try:
                try: sealed.require_provenance()
                except RuntimeError as error: observed = error
            finally: hook_flags[0] = False
            hook_observations.append((numeric_base.__name__, "direct", observed, equality_failure,
                                      tuple(hook_events), tuple(hook_bodies)))
            self.assertIsNone(sealed.require_provenance()); hook_events.clear()
            shared = (numeric,); captured = (shared, shared)
            initial = (frozenset((shared,)), captured)
            def first(value, capture=initial):
                hook_bodies.append("first"); return value
            class Barrier(Mapping):
                def __iter__(self): return iter(())
                def __len__(self): return 0
                def __getitem__(self, key): raise KeyError(key)
                def items(self):
                    if hook_flags[1]:
                        hook_events.append("barrier"); hook_flags[0] = True
                    return ()
            def boundary(value, capture=Barrier()):
                hook_bodies.append("boundary"); return value
            def later(value, capture=captured):
                hook_bodies.append("later"); return value
            sealed = TypedDecoder("fixture.numeric.shared", "1", first, dependencies=(boundary, later))
            self.assertIsNone(sealed.require_provenance()); original_defaults = first.__defaults__
            fresh = (NumericValue(7),); replacement = (frozenset((fresh,)), (fresh, fresh))
            self.assertIsNot(replacement, initial); self.assertIsNot(fresh, shared)
            self.assertEqual(replacement, initial); first.__defaults__ = (replacement,)
            hook_flags[1] = True; observed = None
            try:
                try: sealed.require_provenance()
                except RuntimeError as error: observed = error
            finally:
                first.__defaults__ = original_defaults; hook_flags[:] = [False, False]
            hook_observations.append((numeric_base.__name__, "shared", observed, equality_failure,
                                      tuple(hook_events), tuple(hook_bodies)))
            self.assertIsNone(sealed.require_provenance())
        self.assertEqual([(base, kind) for base, kind, *_ in hook_observations],
                         [("int", "direct"), ("int", "shared"), ("float", "direct"), ("float", "shared")])
        for base, kind, observed, expected, events, bodies in hook_observations:
            self.assertIs(observed, expected, (base, kind, hook_observations))
            self.assertEqual(events, ("numeric_exception",) if kind == "direct" else ("barrier", "numeric_exception"))
            self.assertEqual(bodies, ())

        # Per-instance Field state and unsupported descriptors retain original public behavior.
        from dataclasses import Field, dataclass
        @dataclass(frozen=True, slots=True)
        class FieldCapture:
            left: str
            right: str
        field_value = FieldCapture("left-value", "right-value")
        def field_decode(value, captured=field_value): return value
        mutable_default = {"items": [1]}
        def shared_hook(value, state=mutable_default):
            return value
        def forwarded_hook(value, callback=shared_hook):
            return callback(value)
        def checked_hook(value, context, callback=shared_hook):
            return None
        shared_decoder = TypedDecoder("fixture.operation.shared", "1", forwarded_hook,
                                      dependencies=(shared_hook,))
        shared_check = diagnostics.NamedHostCheck("fixture.operation.check", "1", "selected",
            diagnostics.HostCheckContext, b'{"requirements":[]}', checked_hook, dependencies=(shared_hook,))
        for owner in (shared_decoder, shared_check):
            self.assertIsNone(owner.require_provenance())
        mutable_default["items"][0] = 2
        for owner in (shared_decoder, shared_check):
            with self.assertRaisesRegex(ValueError, "^frozen (typed decoder|named owner) hook provenance changed$"):
                owner.require_provenance()
        mutable_default["items"][0] = 1
        for owner in (shared_decoder, shared_check):
            self.assertIsNone(owner.require_provenance())
        def field_wrapper(value, callback=field_decode):
            return callback(value)
        field_decoder = TypedDecoder("fixture.field.layout", "1", field_wrapper,
                                     dependencies=(field_decode,))
        self.assertIsNone(field_decoder.require_provenance())
        try:
            object.__setattr__(field_value, "right", "changed-value")
            with self.assertRaisesRegex(ValueError, "^frozen typed decoder hook provenance changed$"):
                field_decoder.require_provenance()
        finally: object.__setattr__(field_value, "right", "right-value")
        self.assertIsNone(field_decoder.require_provenance())
        field_records = fields(FieldCapture); later_field = field_records[1]
        try:
            later_field.name = "undeclared-slot"
            with self.assertRaisesRegex(
                    TypeError, "^typed decoder dataclass captures require only declared slot fields$"):
                field_decoder.require_provenance()
        finally: later_field.name = "right"
        self.assertIsNone(field_decoder.require_provenance())
        field_map = FieldCapture.__dataclass_fields__
        try:
            FieldCapture.__dataclass_fields__ = dict(field_map)
            self.assertIsNot(FieldCapture.__dataclass_fields__, field_map)
            self.assertIsNone(field_decoder.require_provenance())
            FieldCapture.__dataclass_fields__ = dict(reversed(tuple(field_map.items())))
            with self.assertRaisesRegex(ValueError, "^frozen typed decoder hook provenance changed$"):
                field_decoder.require_provenance()
        finally: FieldCapture.__dataclass_fields__ = field_map
        self.assertIsNone(field_decoder.require_provenance())
        field_name_slot, field_reads, field_armed = Field.name, [], [False]
        field_error = RuntimeError("fixture Field.name access")
        def observed_field_name(record):
            name = field_name_slot.__get__(record, Field); field_reads.append(name)
            if field_armed[0] and record is later_field: raise field_error
            return name
        try:
            Field.name = property(observed_field_name)
            fallback_decoder = TypedDecoder("fixture.field.fallback", "1", field_decode)
            field_reads.clear(); self.assertIsNone(fallback_decoder.require_provenance())
            self.assertEqual(field_reads, ["left", "right", "left", "right"])
            field_armed[0] = True; field_reads.clear()
            with self.assertRaises(RuntimeError) as field_failure: fallback_decoder.require_provenance()
            self.assertIs(field_failure.exception, field_error)
            self.assertEqual(field_reads, ["left", "right"])
            field_armed[0] = False; field_reads.clear()
            self.assertIsNone(fallback_decoder.require_provenance())
            self.assertEqual(field_reads, ["left", "right", "left", "right"])
        finally: Field.name = field_name_slot; field_armed[0] = False
        self.assertIsNone(field_decoder.require_provenance())
        self.assertIsNone(fallback_decoder.require_provenance())
        # End Field-layout public oracles.

        # End public original-source oracle.

        # One owner's immutable grammar must never retain a payload, export or query result.
        template_schema = {**diagnostics.closed_rule(references['properties']),'$defs':{'text':{'type':'string'}}}
        cached, independent = SchemaRules.declare(template_schema), SchemaRules.declare(template_schema)
        packet = {'left': 'ok', 'right': 'ok'}
        def template_decode(value, captured=cached): return value
        template_decoder = TypedDecoder('fixture.template.owner', '1', template_decode)
        self.assertIsNone(template_decoder.require_provenance())
        parses, preparations, admissions = [], [], []
        def template_profile(frame, event, _result):
            if event == 'call' and frame.f_code is diagnostics.strict_json_object.__code__: parses.append('parse')
            if event == 'call' and frame.f_code is diagnostics._prepare_diagnostic_template.__code__:
                preparations.append(frame.f_locals['root'])
            if event == 'return' and frame.f_code is diagnostics._diagnostic_plain.__code__:
                if frame.f_locals['_certificate'] is not None:
                    admissions.append((len(frame.f_locals['value']), _result))
        previous_profile = sys.getprofile()
        sys.setprofile(template_profile)
        try: self.assertEqual((cached.findings(packet), cached.findings(packet), independent.findings(packet)),
                             ((), (), ()))
        finally: sys.setprofile(previous_profile)
        self.assertEqual(parses, ['parse', 'parse'])
        self.assertEqual(len(preparations), 2); self.assertIsNot(preparations[0], preparations[1])
        self.assertIsNone(template_decoder.require_provenance())
        first_export, second_export = cached.schema_value(), cached.schema_value()
        self.assertIsNot(first_export, second_export)
        self.assertIsNot(first_export['$defs']['text'], second_export['$defs']['text'])
        first_export['$defs']['text']['type'] = 'integer'
        self.assertEqual(second_export, template_schema)
        packet['left'] = 0
        earlier = cached.findings(packet)
        packet.update(left='ok', right=None)
        self.assertEqual((earlier, cached.findings(packet)),
                         (('$.left: expected string, got integer',), ('$.right: expected string, got null',)))
        packet['right'] = 'ok'
        self.assertEqual((cached.findings(packet), independent.findings(packet)), ((), ()))
        foreign = SchemaRules.declare({'type': 'integer'})
        self.assertEqual(foreign.findings(0), ())
        template, anchor = cached._diagnostic_template, cached._diagnostic_anchor
        forged = (cached.canonical_bytes, cached.maximum_bytes, *foreign._diagnostic_template[2:])
        for hints in ((None, anchor), (template, None),
                      (foreign._diagnostic_template, foreign._diagnostic_anchor), (forged, forged)):
            try:
                object.__setattr__(cached, '_diagnostic_template', hints[0])
                object.__setattr__(cached, '_diagnostic_anchor', hints[1])
                self.assertEqual(cached.findings(packet), ())
                self.assertEqual(diagnose_json_text(json.dumps(packet), cached), ())
            finally:
                object.__setattr__(cached, '_diagnostic_template', template)
                object.__setattr__(cached, '_diagnostic_anchor', anchor)
        source_bytes = cached.canonical_bytes
        try:
            object.__setattr__(cached, 'canonical_bytes', foreign.canonical_bytes)
            self.assertEqual(cached.findings(packet), ('$: expected integer, got object',))
            self.assertEqual(diagnose_value(packet, cached), ('$: expected integer, got object',))
            with self.assertRaisesRegex(ValueError, '^frozen typed decoder hook provenance changed$'):
                template_decoder.require_provenance()
            object.__setattr__(cached, 'canonical_bytes', b'{')
            with self.assertRaises(ValueError): cached.findings(packet)
            with self.assertRaises(ValueError): diagnose_json_text(json.dumps(packet), cached)
        finally: object.__setattr__(cached, 'canonical_bytes', source_bytes)
        self.assertIsNone(template_decoder.require_provenance())
        marker = RuntimeError('fixture dynamic diagnosis')
        class DynamicPayload(dict):
            def __getitem__(self, key): raise marker
        dynamic = DynamicPayload(packet)
        self.assertEqual(diagnose_value(dynamic, template_schema), ('$: expected object, got non-JSON value',))
        self.assertEqual(cached.findings(dynamic), ('$: expected object, got non-JSON value',))
        self.assertEqual(diagnose_value(dynamic, cached), ('$: expected object, got non-JSON value',))
        self.assertEqual(cached.findings(packet), ())


        public_owner = SchemaRules.declare(template_schema)
        parses.clear(); preparations.clear()
        sys.setprofile(template_profile)
        try:
            self.assertEqual(diagnose_json_text('{', public_owner), ('$: malformed JSON',))
            self.assertEqual(preparations, [])
            self.assertEqual((diagnose_value(packet, public_owner),
                              diagnose_json_text(json.dumps(packet), public_owner)), ((), ()))
        finally: sys.setprofile(previous_profile)
        self.assertEqual((parses, len(preparations)), (['parse'], 1))
        self.assertEqual(diagnose_value({'left': 0, 'right': None}, public_owner),
                         ('$.left: expected string, got integer', '$.right: expected string, got null'))
        self.assertEqual(diagnose_json_text(json.dumps(packet), public_owner), ())
        self.assertEqual(diagnose_value(packet, public_owner), ())

        for tag, order in (("kind", ("a", "z")), ("response_type", ("z", "a"))):
            grammar = {"anyOf": [{"$ref": "#/$defs/" + name} for name in ("left", "right")], "$defs": {
                name: diagnostics.closed_rule({tag: {"type": "string", "enum": [name]},
                    "z": {"type": "integer"}, "a": {"type": "integer"}}) for name in ("left", "right")}}
            rules = SchemaRules.declare(grammar); value = {tag: "unknown", "a": "bad"}
            tails = {"a": "$.a: expected integer, got string", "z": "$.z: required property is missing"}
            expected = (f'$.{tag}: expected one of ["left", "right"], got string', *(tails[k] for k in order))
            self.assertEqual(diagnose_json_text(json.dumps(value), rules), expected)
            self.assertEqual((diagnose_value(value, grammar), rules.findings(value), rules.findings(value)),
                             (expected, expected, expected))
            self.assertEqual((diagnose_json_text(json.dumps(value), rules), diagnose_value(value, rules)),
                             (expected, expected))
        self.assertEqual(cached.findings(packet), ())
        budget_owner = SchemaRules.declare({"type": "array", "items": {"type": "integer"}})
        self.assertEqual(budget_owner.findings([]), ()); admissions.clear()
        try:
            sys.setprofile(template_profile)
            for length in (262_139, 262_140, 0): self.assertEqual(budget_owner.findings([0] * length), ())
        finally: sys.setprofile(previous_profile)
        self.assertEqual(admissions, [(262_139, True), (262_140, False), (0, True)])

        # Dispatch identities, optional fields, and normalization are independent contracts.
        import copy
        def tagged(identity, variant, field, *, optional_hint=False):
            properties = {'schema_id': {'type': 'string', 'enum': [identity]},
                          'variant_id': {'type': 'string', 'enum': [variant]}, field: {'type': 'string'}}
            required = list(properties)
            if optional_hint: properties['hint'] = {'type': 'string'}
            return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
        variants = [tagged('questions.v1', 'plain', 'message'),
                    tagged('questions.v1', 'hint', 'message', optional_hint=True),
                    tagged('other.v1', 'hint', 'other_message')]
        choices = {'anyOf': variants}; dispatch_owner = SchemaRules.declare(choices)
        packets = [{'schema_id': 'questions.v1', 'variant_id': 'plain', 'message': 'plain'},
                   {'schema_id': 'questions.v1', 'variant_id': 'hint', 'message': 'hint'},
                   {'schema_id': 'other.v1', 'variant_id': 'hint', 'other_message': 'other'}]
        dispatched = []
        def observe_dispatch(frame, event, _result):
            if event == 'call' and frame.f_code is diagnostics._diagnose.__code__ and frame.f_locals['path'] == '$':
                fields = frame.f_locals['schema'].get('properties', {})
                if 'schema_id' in fields and 'variant_id' in fields:
                    dispatched.append((fields['schema_id']['enum'][0], fields['variant_id']['enum'][0]))
        previous = sys.getprofile()
        try:
            sys.setprofile(observe_dispatch)
            for value in packets: self.assertEqual(dispatch_owner.findings(value), ())
            for value in ({**packets[0], 'variant_id': 'unknown'}, {**packets[0], 'schema_id': 'unknown'},
                          {**packets[0], 'variant_id': 1}, {k: v for k, v in packets[0].items() if k != 'variant_id'}):
                self.assertTrue(dispatch_owner.findings(value))
        finally: sys.setprofile(previous)
        self.assertEqual(dispatched, [('questions.v1', 'plain'), ('questions.v1', 'hint'), ('other.v1', 'hint')])
        for value in packets:
            self.assertEqual((diagnose_value(value, choices), dispatch_owner.findings(value)), ((), ()))
        foreign = {**packets[0], 'hint': 7}
        self.assertEqual(dispatch_owner.findings(foreign), ('$: contains 1 unexpected properties (names withheld)',))
        self.assertEqual(dispatch_owner.findings({**packets[1], 'hint': None}), ('$.hint: expected string, got null',))
        self.assertEqual(dispatch_owner.findings({**packets[1], 'hint': 'present'}), ())
        nested = SchemaRules.declare(diagnostics.closed_rule({'left': choices, 'right': choices}))
        pair_value = {'left': packets[1], 'right': packets[2]}
        self.assertEqual((nested.findings(pair_value), nested.findings(pair_value)), ((), ()))
        self.assertEqual(nested.findings({'left': packets[1], 'right': {**packets[2], 'other_message': None}}),
                         ('$.right.other_message: expected string, got null',))
        unique = {'anyOf': [diagnostics.closed_rule({'schema_id': {'type': 'string', 'enum': [name]},
                            name: {'type': 'integer'}}) for name in ('first', 'second')]}
        unique_owner = SchemaRules.declare(unique)
        self.assertEqual(unique_owner.findings({'schema_id': 'second', 'second': 1}), ())
        self.assertEqual(unique_owner.findings({'schema_id': 'second', 'second': 'bad'}),
                         ('$.second: expected integer, got string',))
        from story_writing_agents.structured_output_validation import ContractOwner, ContractIdentity, BoundContract
        decoded_values = []
        def normalized(value):
            result = value.as_object()
            decoded_values.append(result)
            return result
        @dataclass(frozen=True)
        class DispatchOwner(ContractOwner):
            rules: SchemaRules
            owner_id = 'fixture.dispatch'; owner_revision = '1'
            def declare_rules(self): return self.rules
            def typed_decoder(self): return TypedDecoder('fixture.dispatch.value', '1', normalized)
            def named_host_checks(self): return ()
        bound = BoundContract.bind(DispatchOwner(nested), ContractIdentity(b'{}', 65536, '$'))
        supplied = {'schema_id': bound.schema_id, 'left': foreign, 'right': packets[2], 'extension': {'private': True}}
        untouched = copy.deepcopy(supplied)
        expected = {'left': packets[0], 'right': packets[2]}
        self.assertEqual(bound.decode_selected(supplied).value, expected)
        self.assertEqual(bound.decode_selected(json.dumps(supplied)).value, expected)
        self.assertEqual(supplied, untouched)
        with self.assertRaises(ValueError): bound.decode_selected({**supplied, 'left': {**foreign, 'message': 7}})
        with self.assertRaises(ValueError): bound.decode_selected({**supplied, 'left': {**foreign, 'variant_id': 'unknown'}})
        self.assertEqual(bound.decode_selected(supplied).value, expected)
        token = bound.validate_value(supplied)
        returned = token.as_object(); returned['left']['message'] = 'changed return'
        self.assertEqual(bound.decode_validated(token).value, expected)
        another = BoundContract.bind(DispatchOwner(nested), ContractIdentity(b'{"other":true}', 65536, '$'))
        with self.assertRaises(TypeError): another.decode_validated(token)
        self.assertEqual(bound.decode_validated(token).value, expected)
        map_rule = {**diagnostics.closed_rule({'offered': {'type': 'boolean'}}), 'x-swa-property-policy': 'closed-map'}
        map_bound = BoundContract.bind(DispatchOwner(SchemaRules.declare(diagnostics.closed_rule({'selection': map_rule}))),
                                       ContractIdentity(b'{}', 65536, '$'))
        selected_map = {'schema_id': map_bound.schema_id, 'selection': {'offered': True}}
        self.assertEqual(map_bound.decode_selected(selected_map).value, {'selection': {'offered': True}})
        with self.assertRaises(ValueError):
            map_bound.decode_selected({**selected_map, 'selection': {'offered': True, 'unoffered': True}})
        self.assertNotIn('x-swa-property-policy', repr(map_bound.schema_value()))
        self.assertEqual(map_bound.rule_graph.schema_value()['properties']['selection']['x-swa-property-policy'], 'closed-map')
        question = diagnostics.closed_rule({'schema_id': {'type': 'string', 'enum': ['questions.v1']},
            'question': {'type': 'string'}, 'hint': {'type': 'string'},
            'unavailable_id': {'type': 'string', 'enum': []}})
        question['required'] = ['schema_id', 'question']
        question_rules = SchemaRules.declare(diagnostics.closed_rule({'prompt': question}))
        self.assertEqual(question_rules.schema_value()['properties']['prompt'], question)
        question_bound = BoundContract.bind(DispatchOwner(question_rules), ContractIdentity(b'{}', 65536, '$'))
        prompt = {'schema_id': 'questions.v1', 'question': 'Why?', 'hint': 'One clue'}
        offered = {'schema_id': question_bound.schema_id, 'prompt': {**prompt, 'vendor_extension': True}}
        unchanged = copy.deepcopy(offered); prior_decodes = len(decoded_values)
        self.assertEqual(question_bound.decode_selected(offered).value, {'prompt': prompt})
        self.assertEqual(len(decoded_values) - prior_decodes, 1)
        self.assertEqual(offered, unchanged)
        absent = {'schema_id': 'questions.v1', 'question': 'Why?'}
        self.assertEqual(question_bound.decode_selected({**offered, 'prompt': absent}).value, {'prompt': absent})
        for hint in (7, None):
            with self.assertRaises(ValueError):
                question_bound.decode_selected({**offered, 'prompt': {**prompt, 'hint': hint}})
        with self.assertRaises(ValueError):
            question_bound.decode_selected({**offered, 'prompt': {'schema_id': 'questions.v1', 'hint': 'Clue'}})
        projection = question_bound.rule_graph.provider_projection()
        self.assertIs(projection.owner, question_bound.rule_graph)
        native = projection.schema_value()
        self.assertEqual(native, question_bound.schema_value())
        detached = projection.schema_value()
        for exported in (question_bound.schema_value(), detached):
            arms = exported['properties']['prompt']['anyOf']
            self.assertEqual(tuple(tuple(arm['properties']) for arm in arms),
                             (('question', 'schema_id'), ('hint', 'question', 'schema_id')))
            self.assertEqual(tuple(tuple(arm['required']) for arm in arms),
                             (('schema_id', 'question'), ('schema_id', 'question', 'hint')))
        detached['properties']['prompt']['anyOf'][0]['properties'].clear()
        self.assertEqual(projection.schema_value(), native)
        for hint in (7, None):
            self.assertTrue(diagnose_value({**offered, 'prompt': {**prompt, 'hint': hint}}, projection))
        self.assertNotIn('"unavailable_id"', json.dumps(native))
        for unavailable in ('invented', None, 7):
            rejected = {**offered, 'prompt': {**offered['prompt'], 'unavailable_id': unavailable}}
            with self.assertRaises(ValueError): question_bound.decode_selected(rejected)
            self.assertTrue(diagnose_value(rejected, projection))
        with self.assertRaises(ValueError):
            SchemaRules.declare(diagnostics.closed_rule({'unavailable_id': {'type': 'string', 'enum': []}}))
        self.assertEqual(diagnose_value({**offered, 'prompt': prompt}, native), ())
        self.assertEqual(diagnose_value({**offered, 'prompt': absent}, projection), ())
        self.assertEqual(diagnose_json_text(json.dumps({**offered, 'prompt': prompt}), projection), ())
        self.assertTrue(diagnose_value(offered, projection))
        with self.assertRaises(ValueError):
            question_bound.rule_graph.provider_projection({**native, 'additionalProperties': True})
        self.assertEqual(question_bound.decode_selected(offered).value, {'prompt': prompt})
        exported = bound.schema_value(); exported['properties'].clear()
        self.assertEqual(bound.decode_selected(supplied).value, expected)

    def test_malformed_and_duplicate_json_have_value_free_diagnostics(self) -> None:
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        from story_writing_agents.structured_output_validation import SchemaRules
        owner = SchemaRules.declare({**schema, "required": []})
        for text, expected in (("{", ("$: malformed JSON",)), ('{"private":1,"private":2}',
            ("$: duplicate object key",)), ("NaN", ("$: malformed JSON",)), ("{}", ())):
            self.assertEqual(diagnose_json_text(text, schema), expected)
            self.assertEqual((diagnose_json_text(text, owner), diagnose_json_text(text, owner)),
                             (expected, expected))
        private = "PRIVATE_EXCEPTION_AND_VALUE_한🙂"
        failure = validation_failure(ValueError(private), "$.message", "expected nonempty text", value=private)
        with self.assertRaises(ValueError) as caught:
            validation_call("$.result", lambda: (_ for _ in ()).throw(failure))
        self.assertIs(caught.exception, failure); wrapper = carry_validation(TypeError(private), failure)
        self.assertEqual(failure_issues(wrapper, path="$.action"),
                         ("$.action.result.message: expected nonempty text; got string",))
        self.assertNotIn(private, repr(failure_issues(wrapper)))
        with self.assertRaises(FrozenInstanceError): failure.provider_validation_findings[0].requirement = private
        self.assertEqual(failure_issues(ValueError(private)), ()); failure.provider_validation_findings = (private,)
        self.assertEqual(failure_issues(failure), ())
        self.assertEqual(diagnose_value(float("inf"), {"type": "number"}), ("$: expected a finite number, got number",))
        self.assertEqual(diagnose_value(True, {"type": "integer"}), ("$: expected integer, got boolean",))

    def test_properties_omitted_from_required_remain_optional(self) -> None:
        schema = { "type": "object", "properties": {"message": {"type": "string"},
                "optional_count": {"type": "integer"}, }, "required": ["message"], "additionalProperties": False, }
        self.assertEqual(diagnose_json_text('{"message":"ok"}', schema), ())
        self.assertEqual(diagnose_json_text('{"message":"ok","optional_count":"bad"}', schema),
            ("$.optional_count: expected integer, got string",), )
        self.assertEqual(diagnose_json_text("{}", schema), ("$.message: required property is missing",), )
        from story_writing_agents.structured_output_validation import SchemaRules
        optional = SchemaRules.declare(schema)
        self.assertEqual((optional.findings({'message': 'ok'}), optional.findings({'message': 'ok', 'optional_count': 1})),
                         ((), ()))
        self.assertEqual(optional.findings({'message': 'ok', 'optional_count': None}),
                         ('$.optional_count: expected integer, got null',))

    def test_bounded_chunks_preserve_order_and_over_32k_content(self) -> None:
        properties = {f"field_{index:04d}": {"type": "string"} for index in range(1500)}
        schema = {"type": "object", "properties": properties,
                  "required": list(properties), "additionalProperties": False}
        issues = diagnose_json_text("{}", schema); chunks = bounded_chunks(issues)
        self.assertGreater(sum(len(chunk.encode()) for chunk in chunks), 32_768); self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.encode()) < 32_768 for chunk in chunks))
        self.assertEqual(tuple(line for chunk in chunks for line in chunk.splitlines()[1:]), issues)
        properties = {f"field_{index:04d}": {"anyOf": [{"type": "string"}] * 2} for index in range(2050)}
        failure = encoding_failure(ValueError("PRIVATE_ERROR"), {**dict.fromkeys(properties,
            "PRIVATE_VALUE\ud800"), "PRIVATE_KEY": "\ud800"}, {"type": "object", "properties": properties})
        chunks = bounded_chunks(failure_issues(failure))
        lines = tuple(line for chunk in chunks for line in chunk.splitlines()[1:]); self.assertEqual(len(lines), 2049)
        self.assertEqual(len(set(lines)), len(lines))
        self.assertEqual(lines[-1], "$: additional validation issues omitted")
        self.assertEqual(lines[-2], "$.field_2047: expected strict UTF-8 text without unpaired surrogates")
        self.assertTrue(all(len(chunk.encode()) < 32_768 for chunk in chunks))
        self.assertNotIn("PRIVATE", "\n".join(chunks))

        from story_writing_agents.structured_output_validation import SchemaRules
        capped = SchemaRules.declare({'type': 'array', 'items': {'type': 'integer'}})
        capped_issues = capped.findings(['bad'] * 2050)
        self.assertEqual((len(capped_issues), capped_issues[0], capped_issues[-2], capped_issues[-1]),
                         (2049, '$[0]: expected integer, got string',
                          '$[2047]: expected integer, got string', '$: additional validation issues omitted'))
        self.assertEqual(capped.findings([]), ())
        self.assertEqual(capped.findings(['bad']), ('$[0]: expected integer, got string',))
        self.assertEqual(diagnose_json_text(json.dumps(['bad'] * 2050), capped), capped_issues)
        self.assertEqual(diagnose_value([], capped), ())
        self.assertEqual(diagnose_json_text('["bad"]', capped), ('$[0]: expected integer, got string',))
