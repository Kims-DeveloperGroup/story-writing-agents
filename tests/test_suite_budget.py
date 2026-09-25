"""Regression checks for the suite's frozen scope and time gates."""

from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import suite_budget


class SuiteBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.budget = {
            "schema_version": 1,
            "max_sequential_wall_seconds": 1080,
            "max_shard_wall_seconds": 540,
            "max_test_source_lines": 1000,
            "subtest_caps": {"a.test": 2, "b.test": 0},
        }

    def test_new_id_increased_subtests_and_relaxed_time_are_rejected(self) -> None:
        for change in (
            lambda value: value["subtest_caps"].update({"c.test": 0}),
            lambda value: value["subtest_caps"].update({"a.test": 3}),
            lambda value: value.update(max_shard_wall_seconds=541),
            lambda value: value.update(max_sequential_wall_seconds=1081),
        ):
            with self.subTest(change=change):
                candidate = json.loads(json.dumps(self.budget))
                change(candidate)
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(candidate, self.budget)
        candidate = json.loads(json.dumps(self.budget))
        candidate["max_test_source_lines"] += 1
        with self.assertRaises(suite_budget.BudgetError):
            suite_budget.check_monotonic(candidate, self.budget)

    def test_exact_duration_boundary_passes_and_excess_fails(self) -> None:
        reconciliation = {
            "outcome": "success",
            "durations": {"sequential_equivalent_wall_seconds": 1080},
            "shards": [
                {"identity": "1/2", "durations": {"wall_seconds": 540}},
                {"identity": "2/2", "durations": {"wall_seconds": 540}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = [str(Path(directory) / f"{index}.json") for index in (1, 2)]
            for path, identifier, count in zip(paths, ("a.test", "b.test"), (2, 0)):
                Path(path).write_text(json.dumps({"tests": [
                    {"canonical_id": identifier, "subtest_count": count}
                ]}), encoding="utf-8")
            with patch.object(suite_budget.runner, "_reconcile_reports", return_value=reconciliation):
                self.assertIs(suite_budget.check_reports(self.budget, paths), reconciliation)
                reconciliation["shards"][0]["durations"]["wall_seconds"] = 540.001
                with self.assertRaisesRegex(suite_budget.BudgetError, "shard 1/2"):
                    suite_budget.check_reports(self.budget, paths)
                reconciliation["shards"][0]["durations"]["wall_seconds"] = 540
                reconciliation["durations"]["sequential_equivalent_wall_seconds"] = 1080.001
                with self.assertRaisesRegex(suite_budget.BudgetError, "sequential"):
                    suite_budget.check_reports(self.budget, paths)

    def test_subtest_expansion_and_incomplete_shards_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = [str(Path(directory) / f"{index}.json") for index in (1, 2)]
            Path(paths[0]).write_text(json.dumps({"tests": [
                {"canonical_id": "a.test", "subtest_count": 3}
            ]}), encoding="utf-8")
            Path(paths[1]).write_text(json.dumps({"tests": [
                {"canonical_id": "b.test", "subtest_count": 0}
            ]}), encoding="utf-8")
            result = {
                "outcome": "success",
                "durations": {"sequential_equivalent_wall_seconds": 1},
                "shards": [{"identity": "1/2", "durations": {"wall_seconds": 1}}],
            }
            with patch.object(suite_budget.runner, "_reconcile_reports", return_value=result):
                with self.assertRaisesRegex(suite_budget.BudgetError, "subtest scope"):
                    suite_budget.check_reports(self.budget, paths)
            with patch.object(suite_budget.runner, "_reconcile_reports", side_effect=suite_budget.runner.RunnerError("incomplete")):
                with self.assertRaisesRegex(suite_budget.runner.RunnerError, "incomplete"):
                    suite_budget.check_reports(self.budget, paths)

    def test_runner_counts_successful_subtests_in_parent_record(self) -> None:
        class Synthetic(unittest.TestCase):
            def runTest(self) -> None:
                for value in (1, 2):
                    with self.subTest(value=value):
                        if value == 2:
                            self.skipTest("synthetic platform skip")
                        self.assertGreater(value, 0)

        case = Synthetic()
        result = suite_budget.runner.TimedTextTestResult(
            io.StringIO(), descriptions=False, verbosity=0
        )
        unittest.TestSuite([case]).run(result)
        self.assertEqual(result.records([case.id()])[0]["subtest_count"], 2)
