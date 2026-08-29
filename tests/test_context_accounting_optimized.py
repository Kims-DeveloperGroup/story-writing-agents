"""Exact provider-context accounting gates for compact wire v5."""

from __future__ import annotations

import hashlib
import json
import unittest

from tests.context_accounting_baseline_fixture import BASELINE_GOLDEN_PATH
from tests.context_accounting_optimized_fixture import (
    OPTIMIZED_COMPACT_WIRE_VERSION,
    OPTIMIZED_EXPECTED_PHYSICAL_CALLS,
    OPTIMIZED_GOLDEN_PATH,
    OPTIMIZED_REPORT_SCHEMA_VERSION,
    build_provider_context_optimized,
)


_BASELINE_RAW_SHA256 = (
    "c8b3737b97e33fa90d825b4ca41cfda6b3b348d62401689bdfd90dbbd7b214f4"
)
_BASELINE_CANONICAL_SHA256 = (
    "c1b5e26bc73f04cda0e004fff4fcd95bb4e02b9d0ea75a229310dcb3e6ad1392"
)
_OPTIMIZED_RAW_SHA256 = (
    "20b2a2a11028a17fb35e6be0ce8e8107f5166a40912234b367857f9fe63b1a5b"
)
_OPTIMIZED_CANONICAL_SHA256 = (
    "59202c6c617db3c804312db4dea264fb384f1a58697ff19273deb4df4fde427f"
)
_EXPECTED_SCENARIOS = (
    ("fresh_ordinary_conversation", 1, 600, 218, 391, 0, 1209),
    ("continued_ordinary_conversation", 1, 600, 218, 306, 0, 1124),
    ("retrieval_direct_answer", 1, 600, 218, 415, 0, 1233),
    ("retrieval_search_read_answer", 3, 1800, 654, 2525, 0, 4979),
    ("whole_file_reference", 2, 1200, 1366, 1287, 62, 3791),
    ("ranged_file_reference", 2, 1200, 1516, 1286, 19, 3983),
    ("multiple_ordered_references", 2, 1200, 1719, 1560, 81, 4398),
    ("straight_consensus_debate", 5, 3005, 1090, 3018, 0, 7113),
    (
        "conflict_revision_consensus_debate",
        10,
        6031,
        2180,
        6443,
        0,
        14654,
    ),
    ("terminal_deadlock_debate", 9, 5382, 1962, 5624, 0, 12968),
    ("outline_modification", 5, 3005, 1090, 2973, 0, 7068),
    ("character_sheet_modification", 5, 3005, 1090, 3045, 0, 7140),
    ("narrative_rule_modification", 5, 3005, 1090, 3036, 0, 7131),
    ("create_only_manuscript_revision", 5, 3005, 1090, 3167, 0, 7262),
)
_EXPECTED_ACTIONS = {
    "provider_calls": 56,
    "reference_read_batches": 3,
    "ordinary_conversation_calls": 5,
    "retrieval_search_actions": 1,
    "retrieval_transcript_read_actions": 1,
    "retrieval_answer_actions": 2,
    "debate_draft_actions": 7,
    "debate_audit_actions": 27,
    "debate_proposal_actions": 2,
    "debate_synthesis_actions": 2,
    "debate_finalize_actions": 6,
}
_EXPECTED_BYTES = {
    "binding_instruction_utf8_bytes": 33638,
    "schema_utf8_bytes": 15501,
    "payload_utf8_bytes": 35076,
    "raw_mandatory_reference_utf8_bytes": 162,
    "gross_model_input_utf8_bytes": 84215,
    "non_reference_model_input_utf8_bytes": 84053,
    "actual_transport_utf8_bytes": 35076,
}
_EXPECTED_COMPONENTS = (
    ("binding", "binding_structure", None, 224, 0),
    ("binding", "role_system_prompt", None, 8886, 0),
    ("binding", "persona", None, 14056, 0),
    ("binding", "response_language", None, 560, 0),
    ("binding", "provider_constraints", None, 9912, 0),
    ("payload", "output_schema", None, 15501, 0),
    ("payload", "control", None, 13189, 0),
    ("payload", "current_input", None, 3583, 0),
    ("payload", "candidate", None, 2574, 0),
    ("payload", "debate_messages", None, 8687, 0),
    ("payload", "conversation_context", None, 124, 0),
    ("payload", "retrieved_observation", None, 6065, 0),
    ("payload", "mandatory_reference_descriptor", None, 678, 0),
    ("payload", "mandatory_reference_content", None, 176, 162),
)


def _canonical_report_bytes(report: object) -> bytes:
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ProviderContextOptimizedTests(unittest.IsolatedAsyncioTestCase):
    """Freeze the same workload after contextual compact-wire optimization."""

    async def test_optimized_is_repeatable_and_matches_versioned_golden(
        self,
    ) -> None:
        first = await build_provider_context_optimized(self)
        second = await build_provider_context_optimized(self)
        first_bytes = _canonical_report_bytes(first)

        self.assertEqual(first_bytes, _canonical_report_bytes(second))
        self.assertEqual(
            hashlib.sha256(first_bytes).hexdigest(),
            _OPTIMIZED_CANONICAL_SHA256,
        )
        golden_bytes = OPTIMIZED_GOLDEN_PATH.read_bytes()
        self.assertEqual(
            hashlib.sha256(golden_bytes).hexdigest(),
            _OPTIMIZED_RAW_SHA256,
        )
        golden = json.loads(golden_bytes.decode("utf-8"))
        self.assertEqual(first, golden)
        self.assertEqual(
            hashlib.sha256(_canonical_report_bytes(golden)).hexdigest(),
            _OPTIMIZED_CANONICAL_SHA256,
        )
        self._assert_report_contract(first)

    def test_baseline_fixture_identity_is_immutable(self) -> None:
        raw = BASELINE_GOLDEN_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), _BASELINE_RAW_SHA256)
        report = json.loads(raw.decode("utf-8"))
        self.assertEqual(
            hashlib.sha256(_canonical_report_bytes(report)).hexdigest(),
            _BASELINE_CANONICAL_SHA256,
        )
        self.assertEqual(
            report["cumulative"]["byte_totals"][
                "non_reference_model_input_utf8_bytes"
            ],
            180575,
        )

    def _assert_report_contract(self, report: dict[str, object]) -> None:
        self.assertEqual(
            report["schema_version"],
            OPTIMIZED_REPORT_SCHEMA_VERSION,
        )
        quality = report["quality_invariants"]
        self.assertIsInstance(quality, dict)
        assert isinstance(quality, dict)
        for invariant in (
            "provider_free",
            "network_free",
            "subprocess_free",
            "semantic_rag_disabled",
            "fixed_ids_timestamps_responses_revisions_paths_schemas",
            "content_free_report",
            "contextual_runtime",
        ):
            self.assertIs(quality[invariant], True)
        self.assertEqual(
            quality["compact_wire_version"],
            OPTIMIZED_COMPACT_WIRE_VERSION,
        )
        self.assertEqual(quality["canonical_manuscript_directory"], "manuscript/")

        scenarios = report["scenarios"]
        cumulative = report["cumulative"]
        self.assertIsInstance(scenarios, list)
        self.assertIsInstance(cumulative, dict)
        assert isinstance(scenarios, list)
        assert isinstance(cumulative, dict)
        actual_rows = []
        for scenario in scenarios:
            totals = scenario["byte_totals"]
            actual_rows.append(
                (
                    scenario["scenario_id"],
                    scenario["physical_provider_calls"],
                    totals["binding_instruction_utf8_bytes"],
                    totals["schema_utf8_bytes"],
                    totals["payload_utf8_bytes"],
                    totals["raw_mandatory_reference_utf8_bytes"],
                    totals["non_reference_model_input_utf8_bytes"],
                )
            )
        self.assertEqual(tuple(actual_rows), _EXPECTED_SCENARIOS)

        calls = [call for scenario in scenarios for call in scenario["calls"]]
        self.assertEqual(len(calls), OPTIMIZED_EXPECTED_PHYSICAL_CALLS)
        self.assertEqual(cumulative["scenario_count"], 14)
        self.assertEqual(cumulative["physical_provider_calls"], 56)
        self.assertEqual(cumulative["action_calls"], _EXPECTED_ACTIONS)
        self.assertEqual(cumulative["byte_totals"], _EXPECTED_BYTES)
        self.assertEqual(cumulative["controlled_provider_elapsed_ns"], 257_000_000)
        self.assertIsNone(cumulative["provider_usage"])
        self.assertEqual(
            tuple(
                (
                    item["source"],
                    item["kind"],
                    item["identity"],
                    item["serialized_utf8_bytes"],
                    item["raw_mandatory_reference_utf8_bytes"],
                )
                for item in cumulative["component_totals"]
            ),
            _EXPECTED_COMPONENTS,
        )

        reference_calls = [
            call
            for call in calls
            if call["kind"] == "writing_artifact_reference_read"
        ]
        substantive_calls = [call for call in calls if call not in reference_calls]
        self.assertEqual(len(reference_calls), 3)
        self.assertEqual(len(substantive_calls), 53)
        self.assertTrue(all(call["schema_utf8_bytes"] == 218 for call in substantive_calls))
        self.assertEqual(
            sum(call["raw_mandatory_reference_utf8_bytes"] for call in calls),
            162,
        )
        self.assertEqual(
            [
                scenario["byte_totals"]["raw_mandatory_reference_utf8_bytes"]
                for scenario in scenarios[4:7]
            ],
            [62, 19, 81],
        )

        byte_keys = tuple(_EXPECTED_BYTES)
        for scenario in scenarios:
            scenario_calls = scenario["calls"]
            self.assertEqual(
                [call["ordinal"] for call in scenario_calls],
                list(range(1, len(scenario_calls) + 1)),
            )
            self.assertEqual(
                scenario["action_calls"]["provider_calls"],
                len(scenario_calls),
            )
            for key in byte_keys:
                self.assertEqual(
                    scenario["byte_totals"][key],
                    sum(call[key] for call in scenario_calls),
                )
        for call in calls:
            self.assertEqual(call["state"], "submitted")
            self.assertIsNone(call["usage"])
            self.assertEqual(
                call["binding_instruction_utf8_bytes"],
                sum(
                    item["serialized_utf8_bytes"]
                    for item in call["binding_components"]
                ),
            )
            self.assertEqual(
                call["schema_utf8_bytes"] + call["payload_utf8_bytes"],
                sum(
                    item["serialized_utf8_bytes"]
                    for item in call["payload_components"]
                ),
            )
            self.assertEqual(
                call["gross_model_input_utf8_bytes"],
                call["binding_instruction_utf8_bytes"]
                + call["schema_utf8_bytes"]
                + call["payload_utf8_bytes"],
            )
            self.assertEqual(
                call["non_reference_model_input_utf8_bytes"],
                call["gross_model_input_utf8_bytes"]
                - call["raw_mandatory_reference_utf8_bytes"],
            )
            self.assertEqual(
                call["actual_transport_utf8_bytes"],
                call["payload_utf8_bytes"],
            )

        by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
        for scenario_id in (
            "fresh_ordinary_conversation",
            "continued_ordinary_conversation",
            "retrieval_direct_answer",
        ):
            self.assertEqual(by_id[scenario_id]["physical_provider_calls"], 1)
        for scenario_id in (
            "whole_file_reference",
            "ranged_file_reference",
            "multiple_ordered_references",
        ):
            self.assertEqual(by_id[scenario_id]["physical_provider_calls"], 2)
            self.assertEqual(
                by_id[scenario_id]["action_calls"]["ordinary_conversation_calls"],
                1,
            )
        self.assertEqual(
            by_id["retrieval_search_read_answer"]["physical_provider_calls"],
            3,
        )

        comparison = report["baseline_comparison"]
        self.assertEqual(comparison["baseline_non_reference_utf8_bytes"], 180575)
        self.assertEqual(comparison["maximum_optimized_utf8_bytes"], 90287)
        self.assertEqual(comparison["optimized_non_reference_utf8_bytes"], 84053)
        self.assertIs(comparison["passes_50_percent_reduction"], True)
        self.assertLessEqual(84053 * 2, 180575)
        self.assertLessEqual(84053, 84104)
        self.assertLessEqual(33638, 35500)


if __name__ == "__main__":
    unittest.main()
