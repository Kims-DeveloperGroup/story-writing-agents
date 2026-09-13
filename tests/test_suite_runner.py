"""Focused behavior tests for the canonical unittest timing runner."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests import runner


def _case_type(name: str, **methods):
    return type(name, (unittest.TestCase,), {"__module__": "synthetic.runner", **methods})


def _collection(*tests: unittest.TestCase) -> runner.CanonicalCollection:
    ordered = tuple(sorted(tests, key=lambda test: test.id()))
    return runner.CanonicalCollection(ordered, tuple(test.id() for test in ordered))


class CanonicalCollectionTests(unittest.TestCase):
    def test_discovery_uses_repository_top_level_and_stable_sorted_ids(self):
        Case = _case_type("Case", test_z=lambda self: None, test_a=lambda self: None)

        class Loader:
            errors: list[str] = []

            def discover(self, **kwargs):
                self.arguments = kwargs
                return unittest.TestSuite((Case("test_z"), Case("test_a")))

        loader = Loader()
        root = Path("/synthetic/repository")
        collection = runner._discover_canonical(loader=loader, repository_root=root)
        self.assertEqual(
            loader.arguments,
            {"start_dir": str(root / "tests"), "pattern": "test*.py", "top_level_dir": str(root)},
        )
        self.assertEqual(collection.ids, tuple(sorted(collection.ids)))
        manifest = runner._manifest(collection.ids)
        expected = hashlib.sha256(
            "".join(f"{identifier}\n" for identifier in collection.ids).encode()
        ).hexdigest()
        self.assertEqual(manifest["digest"], f"sha256:{expected}")
        self.assertEqual(manifest["ordered_ids"], list(collection.ids))

    def test_loader_errors_duplicates_and_empty_collection_are_fatal(self):
        Case = _case_type("DuplicateCase", test_value=lambda self: None)
        with self.assertRaisesRegex(runner.RunnerError, "collection failed"):
            runner._canonicalize(unittest.TestSuite(), loader_errors=("broken import",))
        with self.assertRaisesRegex(runner.RunnerError, "no tests"):
            runner._canonicalize(unittest.TestSuite())
        with self.assertRaisesRegex(runner.RunnerError, "duplicate canonical"):
            runner._canonicalize(
                unittest.TestSuite((Case("test_value"), Case("test_value")))
            )

    def test_exact_id_and_module_selectors_form_a_validated_union(self):
        First = _case_type("First", test_one=lambda self: None)
        Second = type(
            "Second",
            (unittest.TestCase,),
            {"__module__": "synthetic.other", "test_two": lambda self: None},
        )
        collection = _collection(First("test_one"), Second("test_two"))
        first_id = next(identifier for identifier in collection.ids if ".First." in identifier)
        selected = runner._select(
            collection, ids=(first_id,), modules=("synthetic.other",)
        )
        self.assertEqual(selected.ids, collection.ids)
        for selection in (
            {"ids": ("missing",)},
            {"modules": ("missing.module",)},
            {"ids": ("",)},
        ):
            with self.subTest(selection=selection), self.assertRaises(runner.RunnerError):
                runner._select(collection, **selection)
        with self.assertRaisesRegex(runner.RunnerError, "no tests"):
            runner._select(runner.CanonicalCollection((), ()))


class TimedResultTests(unittest.TestCase):
    def test_result_preserves_unittest_statuses_subtests_and_cleanup_errors(self):
        def fail(test):
            test.fail("failure")

        def error(_test):
            raise RuntimeError("error")

        @unittest.skip("later")
        def skipped(_test):
            pass

        @unittest.expectedFailure
        def expected(test):
            test.fail("expected")

        @unittest.expectedFailure
        def unexpected(_test):
            pass

        def subtest(test):
            with test.subTest(value=1):
                test.fail("subtest")

        def cleanup(test):
            test.addCleanup(lambda: (_ for _ in ()).throw(RuntimeError("cleanup")))

        Case = _case_type(
            "StatusCase", test_pass=lambda self: None, test_fail=fail, test_error=error,
            test_skip=skipped, test_expected=expected, test_unexpected=unexpected,
            test_subtest=subtest, test_cleanup=cleanup,
        )
        selection = _collection(*(Case(name) for name in unittest.defaultTestLoader.getTestCaseNames(Case)))
        execution = runner._execute(selection, stream=io.StringIO())
        report = runner._run_report(selection, selection, execution, slowest=50)
        statuses = {row["canonical_id"]: row["status"] for row in report["tests"]}
        self.assertIn("passed", statuses.values())
        self.assertIn("failed", statuses.values())
        self.assertIn("error", statuses.values())
        self.assertIn("skipped", statuses.values())
        self.assertIn("expected_failure", statuses.values())
        self.assertIn("unexpected_success", statuses.values())
        self.assertEqual(report["outcome"], "failure")
        self.assertEqual(report["unexecuted_ids"], [])
        self.assertTrue(all(row["duration_seconds"] >= 0 for row in report["tests"]))
        detail_kinds = {detail["kind"] for detail in report["details"]}
        self.assertTrue({"failure", "error", "skip", "expected_failure",
                         "unexpected_success", "subtest_failure"} <= detail_kinds)

    def test_failfast_reports_remaining_ids_as_unexecuted(self):
        def fail(test):
            test.fail("stop")

        Case = _case_type("FailfastCase", test_a_failure=fail, test_b_later=lambda self: None)
        selection = _collection(Case("test_a_failure"), Case("test_b_later"))
        execution = runner._execute(selection, stream=io.StringIO(), failfast=True)
        report = runner._run_report(selection, selection, execution, slowest=1)
        self.assertEqual(report["totals"]["started"], 1)
        self.assertEqual(report["unexecuted_ids"], [selection.ids[1]])
        self.assertEqual(report["slowest"][0]["canonical_id"], selection.ids[0])

    def test_keyboard_interrupt_returns_130_and_writes_partial_json(self):
        def interrupt(_test):
            raise KeyboardInterrupt

        Case = _case_type("InterruptCase", test_a_interrupt=interrupt, test_b_later=lambda self: None)
        collection = _collection(Case("test_a_interrupt"), Case("test_b_later"))
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "result.json"
            with mock.patch.object(runner, "_discover_canonical", return_value=collection):
                with redirect_stderr(io.StringIO()):
                    code = runner.main(("run", "--json", str(evidence)))
            report = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(code, 130)
        self.assertEqual(report["outcome"], "interrupted")
        self.assertEqual(report["totals"]["interrupted"], 1)
        self.assertEqual(report["unexecuted_ids"], [collection.ids[1]])


class RunnerCliTests(unittest.TestCase):
    def setUp(self):
        Case = _case_type("CliCase", test_one=lambda self: None, test_two=lambda self: None)
        self.collection = _collection(Case("test_one"), Case("test_two"))

    def test_list_supports_human_and_machine_readable_manifests(self):
        with mock.patch.object(runner, "_discover_canonical", return_value=self.collection):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(runner.main(("list", "--id", self.collection.ids[0])), 0)
            self.assertIn(self.collection.ids[0], output.getvalue())
            self.assertIn("selected: 1", output.getvalue())
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "manifest.json"
                self.assertEqual(runner.main(("list", "--json", str(path))), 0)
                payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["kind"], "manifest")
        self.assertEqual(payload["selection_manifest"]["ordered_ids"], list(self.collection.ids))

    def test_invalid_selection_exits_two_before_execution(self):
        with mock.patch.object(runner, "_discover_canonical", return_value=self.collection), \
             mock.patch.object(runner, "_execute") as execute, redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(("run", "--id", "missing")), 2)
            self.assertEqual(runner.main(("run", "--slowest", "-1")), 2)
        execute.assert_not_called()
        with mock.patch.object(
            runner, "_discover_canonical", side_effect=runner.RunnerError("loader error")
        ), mock.patch.object(runner, "_execute") as execute, redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(("run",)), 2)
        execute.assert_not_called()

    def test_run_json_contains_manifests_totals_timing_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            human = io.StringIO()
            with mock.patch.object(runner, "_discover_canonical", return_value=self.collection), \
                 redirect_stderr(human):
                code = runner.main(("run", "--json", str(path), "--slowest", "1"))
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["outcome"], "success")
        self.assertEqual(payload["totals"]["selected"], 2)
        self.assertEqual(len(payload["slowest"]), 1)
        self.assertIn("wall_seconds", payload["durations"])
        self.assertIn("python_version", payload["environment"])
        self.assertEqual(payload["canonical_manifest"], payload["selection_manifest"])
        self.assertIn("timing: wall=", human.getvalue())
        self.assertIn("slowest tests:", human.getvalue())

        def fail(test):
            test.fail("failure")

        def error(_test):
            raise RuntimeError("error")

        @unittest.expectedFailure
        def unexpected(_test):
            pass

        FailingCase = _case_type(
            "CliFailureCase", test_failure=fail, test_error=error,
            test_unexpected_success=unexpected,
        )
        failing = _collection(*(
            FailingCase(name)
            for name in unittest.defaultTestLoader.getTestCaseNames(FailingCase)
        ))
        with mock.patch.object(runner, "_discover_canonical", return_value=failing), \
             redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(("run",)), 1)


if __name__ == "__main__":
    unittest.main()
