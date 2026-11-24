"""Existing structured validation contract for bounded findings."""

import json

from story_writing_agents.structured_output_validation import (
    diagnose_json_text,
    diagnose_value,
    encoding_failure,
)
from story_writing_agents.validation_findings import bounded_chunks, failure_issues


def verify_bounded_chunks_preserve_order_and_over_32k_content(self) -> None:
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
