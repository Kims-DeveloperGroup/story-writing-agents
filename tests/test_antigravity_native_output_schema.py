"""Independent Antigravity native output schema case definitions."""

from story_writing_agents.agent_context import canonical_provider_json
from story_writing_agents.antigravity_adapter import AntigravityAdapter


def _singleton_correlation_schema():
    """Synthetic public shape from the native compatibility regression."""
    return {
        "type": "object",
        "properties": {
            "round_number": {"type": "null", "enum": [None]},
            "invocation_ordinal": {"type": "integer", "enum": [1]},
            "attempt": {"type": "integer", "enum": [1]},
        },
    }


class AntigravitySchemaRenderingCases:
    """Verify equivalent schema transport without optional dependencies."""

    def test_nested_schema_positions_render_exact_null_and_integer_singletons(self):
        leaf = {"type": "integer", "enum": [1], "description": "retained"}
        expected = {"type": "integer", "minimum": 1, "maximum": 1,
                    "description": "retained"}
        maps = ("properties", "patternProperties", "$defs", "definitions",
                "dependentSchemas", "dependencies")
        arrays = ("allOf", "anyOf", "oneOf", "prefixItems", "items")
        values = ("additionalProperties", "additionalItems", "unevaluatedProperties",
                  "unevaluatedItems", "items", "contains", "propertyNames", "not",
                  "if", "then", "else", "contentSchema")
        for keyword in maps:
            with self.subTest(keyword=keyword):
                self.assertEqual(AntigravityAdapter.native_output_schema(
                    {keyword: {"x": leaf, "boolean": True, "dependency": ["name"]}}),
                    {keyword: {"x": expected, "boolean": True, "dependency": ["name"]}})
        for keyword in arrays:
            with self.subTest(keyword=keyword):
                self.assertEqual(AntigravityAdapter.native_output_schema(
                    {keyword: [leaf, False]}), {keyword: [expected, False]})
        for keyword in values:
            with self.subTest(keyword=keyword):
                self.assertEqual(AntigravityAdapter.native_output_schema(
                    {keyword: leaf}), {keyword: expected})
        nested = {"properties": {"action": {"allOf": [_singleton_correlation_schema()]}}}
        result = AntigravityAdapter.native_output_schema(nested)
        properties = result["properties"]["action"]["allOf"][0]["properties"]
        self.assertEqual(properties["round_number"], {"type": "null"})
        self.assertEqual(properties["attempt"], {"type": "integer", "minimum": 1, "maximum": 1})
        self.assertEqual(len(canonical_provider_json(result)) - len(canonical_provider_json(nested)), 12)

    def test_annotations_and_non_target_constraints_remain_byte_exact(self):
        targets = {"type": "integer", "enum": [1]}
        annotations = {name: targets for name in ("const", "default", "examples", "custom")}
        annotations["enum"] = [targets]
        self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(annotations)),
                         canonical_provider_json(annotations))
        cases = [
            {"type": "integer", "enum": value}
            for value in ([], [1, 2], [None, 1], [True], [1.0], [2**53], [-2**53])
        ]
        cases += [{"type": kind, "enum": [1]} for kind in ("number", "string", ["integer", "null"])]
        cases += [{"type": "integer", "enum": [1], bound: 0}
                  for bound in ("minimum", "maximum")]
        cases += [{"type": "null", "enum": [False]}, {"enum": [1]}]
        for node in cases:
            with self.subTest(node=node):
                self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(node)),
                                 canonical_provider_json(node))

    def test_rendering_is_deeply_immutable_idempotent_and_order_preserving(self):
        shared = {"description": "first", "enum": [2], "type": "integer", "title": "last"}
        original = {"properties": {"a": shared, "b": shared}, "default": shared}
        before = canonical_provider_json(original)
        rendered = AntigravityAdapter.native_output_schema(original)
        self.assertEqual(canonical_provider_json(original), before)
        self.assertEqual(list(rendered["properties"]["a"]),
                         ["description", "minimum", "maximum", "type", "title"])
        self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(rendered)),
                         canonical_provider_json(rendered))
        rendered["properties"]["a"]["title"] = "changed"
        rendered["default"]["enum"].append(3)
        self.assertEqual(canonical_provider_json(original), before)
        self.assertEqual(rendered["properties"]["b"]["title"], "last")
        no_target = {"title": "ordered", "additionalProperties": False, "type": "object"}
        self.assertEqual(canonical_provider_json(AntigravityAdapter.native_output_schema(no_target)),
                         canonical_provider_json(no_target))

    def test_safe_integer_edges_and_original_constraint_equivalence(self):
        for number in (0, -1, 1, -(2**53 - 1), 2**53 - 1):
            original = {"type": "integer", "enum": [number], "exclusiveMinimum": number - 1}
            lowered = AntigravityAdapter.native_output_schema(original)
            self.assertEqual(lowered, {"type": "integer", "minimum": number,
                                      "maximum": number, "exclusiveMinimum": number - 1})
            for candidate in (number - 1, number, number + 1, None, True, str(number), number + .5):
                # Exact typed runtime scalar contract; no undeclared schema validator.
                original_accepts = type(candidate) is int and candidate == number
                lowered_accepts = (type(candidate) is int and lowered["minimum"] <= candidate
                                   <= lowered["maximum"] and candidate > lowered["exclusiveMinimum"])
                self.assertEqual(original_accepts, lowered_accepts)
        impossible = {"type": "integer", "enum": [1], "exclusiveMinimum": 1}
        self.assertEqual(AntigravityAdapter.native_output_schema(impossible),
                         {"type": "integer", "minimum": 1, "maximum": 1, "exclusiveMinimum": 1})
