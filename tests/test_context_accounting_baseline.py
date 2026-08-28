"""Versioned provider-free baseline for exact provider-context accounting."""

from __future__ import annotations

import hashlib
import json
import unittest

from tests.context_accounting_baseline_fixture import (
    BASELINE_EXPECTED_PHYSICAL_CALLS,
    BASELINE_GOLDEN_PATH,
    BASELINE_SCHEMA_VERSION,
    build_provider_context_baseline,
)


def _canonical_report_bytes(report: object) -> bytes:
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ProviderContextBaselineTests(unittest.IsolatedAsyncioTestCase):
    """Freeze one deterministic report derived from real runtime calls."""

    async def test_baseline_is_repeatable_and_matches_versioned_golden(
        self,
    ) -> None:
        first = await build_provider_context_baseline(self)
        second = await build_provider_context_baseline(self)

        first_bytes = _canonical_report_bytes(first)
        self.assertEqual(first_bytes, _canonical_report_bytes(second))
        golden_bytes = BASELINE_GOLDEN_PATH.read_bytes()
        golden = json.loads(golden_bytes.decode("utf-8"))
        self.assertEqual(first, golden)
        self.assertEqual(
            hashlib.sha256(first_bytes).hexdigest(),
            hashlib.sha256(_canonical_report_bytes(golden)).hexdigest(),
        )
        self._assert_report_contract(first)

    def _assert_report_contract(self, report: dict[str, object]) -> None:
        self.assertEqual(report["schema_version"], BASELINE_SCHEMA_VERSION)
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
        ):
            self.assertIs(quality[invariant], True)
        self.assertEqual(quality["canonical_manuscript_directory"], "manuscript/")

        scenarios = report["scenarios"]
        cumulative = report["cumulative"]
        self.assertIsInstance(scenarios, list)
        self.assertIsInstance(cumulative, dict)
        assert isinstance(scenarios, list)
        assert isinstance(cumulative, dict)
        self.assertEqual(
            [scenario["scenario_id"] for scenario in scenarios],
            [
                "fresh_ordinary_conversation",
                "continued_ordinary_conversation",
                "retrieval_direct_answer",
                "retrieval_search_read_answer",
                "whole_file_reference",
                "ranged_file_reference",
                "multiple_ordered_references",
                "straight_consensus_debate",
                "conflict_revision_consensus_debate",
                "terminal_deadlock_debate",
                "outline_modification",
                "character_sheet_modification",
                "narrative_rule_modification",
                "create_only_manuscript_revision",
            ],
        )
        self.assertEqual(
            [scenario["physical_provider_calls"] for scenario in scenarios],
            [1, 1, 1, 3, 2, 2, 2, 5, 10, 9, 5, 5, 5, 5],
        )
        calls = [
            call
            for scenario in scenarios
            for call in scenario["calls"]
        ]
        self.assertEqual(len(calls), BASELINE_EXPECTED_PHYSICAL_CALLS)
        self.assertEqual(cumulative["scenario_count"], len(scenarios))
        self.assertEqual(cumulative["physical_provider_calls"], len(calls))
        self.assertEqual(
            cumulative["action_calls"]["provider_calls"],
            len(calls),
        )
        self.assertIsNone(cumulative["provider_usage"])

        byte_keys = tuple(cumulative["byte_totals"])
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
            self.assertEqual(
                scenario["controlled_provider_elapsed_ns"],
                sum(
                    call["controlled_provider_elapsed_ns"]
                    for call in scenario_calls
                ),
            )
            self.assertEqual(
                self._component_totals(scenario_calls),
                self._reported_component_totals(scenario["component_totals"]),
            )
        for call in calls:
            self.assertEqual(call["state"], "submitted")
            self.assertIsNone(call["usage"])
            self.assertEqual(
                call["binding_instruction_utf8_bytes"],
                sum(
                    component["serialized_utf8_bytes"]
                    for component in call["binding_components"]
                ),
            )
            self.assertEqual(
                call["schema_utf8_bytes"] + call["payload_utf8_bytes"],
                sum(
                    component["serialized_utf8_bytes"]
                    for component in call["payload_components"]
                ),
            )
            self.assertEqual(
                call["raw_mandatory_reference_utf8_bytes"],
                sum(
                    component["raw_mandatory_reference_utf8_bytes"]
                    for component in call["payload_components"]
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
        for key in byte_keys:
            self.assertEqual(
                cumulative["byte_totals"][key],
                sum(call[key] for call in calls),
            )
        for key in cumulative["action_calls"]:
            self.assertEqual(
                cumulative["action_calls"][key],
                sum(scenario["action_calls"][key] for scenario in scenarios),
            )
        self.assertEqual(
            self._component_totals(calls),
            self._reported_component_totals(cumulative["component_totals"]),
        )
        self.assertEqual(
            cumulative["controlled_provider_elapsed_ns"],
            sum(call["controlled_provider_elapsed_ns"] for call in calls),
        )

        ceiling = report["later_reduction_ceiling"]
        self.assertIsInstance(ceiling, dict)
        assert isinstance(ceiling, dict)
        baseline_bytes = cumulative["byte_totals"][
            "non_reference_model_input_utf8_bytes"
        ]
        self.assertEqual(ceiling["ratio_numerator"], 1)
        self.assertEqual(ceiling["ratio_denominator"], 2)
        self.assertEqual(ceiling["baseline_utf8_bytes"], baseline_bytes)
        self.assertEqual(
            ceiling["maximum_candidate_utf8_bytes"],
            baseline_bytes // 2,
        )

    @staticmethod
    def _component_totals(
        calls: list[dict[str, object]],
    ) -> dict[tuple[object, object, object], tuple[int, int]]:
        totals: dict[tuple[object, object, object], list[int]] = {}
        for call in calls:
            components = [
                *call["binding_components"],  # type: ignore[misc]
                *call["payload_components"],  # type: ignore[misc]
            ]
            for component in components:
                key = (
                    component["source"],
                    component["kind"],
                    component["identity"],
                )
                values = totals.setdefault(key, [0, 0])
                values[0] += component["serialized_utf8_bytes"]
                values[1] += component[
                    "raw_mandatory_reference_utf8_bytes"
                ]
        return {key: (values[0], values[1]) for key, values in totals.items()}

    @staticmethod
    def _reported_component_totals(
        components: list[dict[str, object]],
    ) -> dict[tuple[object, object, object], tuple[int, int]]:
        return {
            (
                component["source"],
                component["kind"],
                component["identity"],
            ): (
                component["serialized_utf8_bytes"],
                component["raw_mandatory_reference_utf8_bytes"],
            )
            for component in components
        }


if __name__ == "__main__":
    unittest.main()
