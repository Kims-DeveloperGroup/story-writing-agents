"""Regression checks for the suite's frozen scope and time gates."""

from __future__ import annotations

from contextlib import nullcontext
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from tests import runner, suite_budget, suite_policy


class SuiteBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.budget = { "schema_version": 1, "max_sequential_wall_seconds": 1080, "max_shard_wall_seconds": 540, "max_test_source_lines": 1000,
            "subtest_caps": {"a.test": 2, "b.test": 0, "c.control": 0, "d.control": 0,
                             suite_policy.ISOLATED_TEST_ID: 0}, }

    def _reports(self, directory, counts):
        ids = tuple(self.budget["subtest_caps"])
        self.weights = suite_policy.TimingWeights(1, 2, {"a": 9, "b": 5, "c": 3}, "retained strict fixture", "sha256:" + "c" * 64, "fixture")
        self.plan = suite_policy.partition_full_shards(ids, self.weights)
        inventory = Path(directory) / "inventory.json"
        inventory.write_text(json.dumps(self.budget), encoding="utf-8")
        self.enterContext(patch.object(runner, "CANONICAL_INVENTORY_PATH", inventory))
        self.enterContext(patch.object(suite_policy, "load_timing_weights", return_value=self.weights))

        class Control(unittest.FunctionTestCase):
            def id(self): return self._description

        cases = tuple(Control(lambda: None, description=identifier) for identifier in ids)
        canonical = runner.CanonicalCollection(cases, ids)
        self.report_data = []
        for index, selected_ids in enumerate(self.plan.shard_ids):
            selected = runner.CanonicalCollection(tuple(cases[ids.index(value)] for value in selected_ids), selected_ids)
            report = runner._run_report(canonical, selected, runner._execute(selected, stream=io.StringIO()), slowest=0,
                                        policy_metadata={"suite_policy": runner._suite_policy_metadata(self.weights, self.plan),
                                                         "selection": {"tiers": [], "shard": f"{index + 1}/5"}})
            maximum = self.budget["max_shard_wall_seconds"]
            report["durations"].update(wall_seconds=maximum if index == 0 else (
                self.budget["max_sequential_wall_seconds"] - maximum) / 4, cumulative_test_seconds=1)
            report["tests"][0].update(duration_seconds=1, subtest_count=(*counts, 0, 0, 0)[index])
            self.report_data.append(report)
        paths = [str(Path(directory) / f"{index}.json") for index in range(1, 6)]
        for path, value in zip(paths, self.report_data):
            Path(path).write_text(json.dumps(value), encoding="utf-8")
        return paths

    def test_new_id_increased_subtests_and_relaxed_time_are_rejected(self) -> None:
        hourly = dict(self.budget, max_sequential_wall_seconds=18000, max_shard_wall_seconds=3600)
        self.assertIsNone(suite_budget.check_monotonic(hourly, self.budget))
        source_base = dict(hourly, max_test_source_lines=142474)
        source = dict(source_base, max_test_source_lines=142604)
        self.assertIsNone(suite_budget.check_monotonic(source, source_base))
        for old, limit in ((142474, 142603), (142474, 142605),
                           (142473, 142604), (142475, 142604), (142604, 142605)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                              dict(source, max_test_source_lines=limit),
                              dict(source_base, max_test_source_lines=old))
        for key, value in (
            ("max_sequential_wall_seconds", 17999),
            ("max_shard_wall_seconds", 3599),
            ("subtest_caps", {**source_base["subtest_caps"], "a.test": 1}),
            ("subtest_caps", {key: cap for key, cap in source_base["subtest_caps"].items() if key != "b.test"}),
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                              {**source, key: value}, source_base)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                          source, dict(self.budget, max_test_source_lines=142474))
        for change in [lambda value: value["subtest_caps"].update({"c.test": 0}),
                lambda value: value["subtest_caps"].update({"a.test": 3}),
                lambda value: value.update(max_shard_wall_seconds=value["max_shard_wall_seconds"] + 1),
                lambda value: value.update(max_sequential_wall_seconds=value["max_sequential_wall_seconds"] + 1)]:
            with self.subTest(change=change):
                for base, origin in ((self.budget, self.budget), (self.budget, hourly), (hourly, hourly),
                                     (source_base, source)):
                    candidate = json.loads(json.dumps(origin))
                    change(candidate)
                    with self.assertRaises(suite_budget.BudgetError):
                        suite_budget.check_monotonic(candidate, base)
        for base, field, value in [ (self.budget, "max_test_source_lines", 1001),
            (self.budget, "max_shard_wall_seconds", 540), (self.budget, "max_sequential_wall_seconds", 1080),
            ({**self.budget, "max_shard_wall_seconds": 539}, "max_shard_wall_seconds", 3600), ]:
            with self.assertRaises(suite_budget.BudgetError):
                suite_budget.check_monotonic({**hourly, field: value}, base)

        from types import SimpleNamespace
        import os, sys, time
        ids = runner._HOSTED_METHOD_IDS
        self.assertEqual(runner._manifest(ids)["digest"],
                         "sha256:131866173146033667a64af8c3dc6017a78671eb501bf4db4d97cc5e722521d7")
        self.assertEqual((len(ids), len({value.rsplit(".", 1)[0] for value in ids})), (147, 17))
        cases = tuple(Mock(id=Mock(return_value=value)) for value in (*ids, "unreviewed.test"))
        canonical = runner.CanonicalCollection(cases, (*ids, "unreviewed.test"))
        serial, scheduled = runner._hosted_partition(canonical, canonical)
        self.assertEqual((serial.tests, scheduled), ((cases[-1],), ids))
        isolated = runner.CanonicalCollection((cases[ids.index(suite_policy.ISOLATED_TEST_ID)],),
                                             (suite_policy.ISOLATED_TEST_ID,))
        serial, scheduled = runner._hosted_partition(canonical, isolated)
        self.assertEqual((serial.ids, scheduled), ((), (suite_policy.ISOLATED_TEST_ID,)))
        launch_digest = "sha256:1c8347ab95d8eb9a217a64542af45db5a82a278ce56c427fa5f6e8b0ce86a4f1"
        self.assertEqual(runner._manifest(runner._hosted_launch_order(ids))["digest"], launch_digest)
        self.assertEqual(runner._hosted_launch_order(ids[::-1]), runner._hosted_launch_order(ids))
        self.assertEqual(runner._hosted_launch_order((ids[-1], ids[0])), (ids[0], ids[-1]))
        drift = runner.CanonicalCollection(cases, (*ids, ids[0].rsplit(".", 1)[0] + ".test_unreviewed"))
        self.assertRaises(runner.RunnerError, runner._hosted_partition, drift, drift)
        args = SimpleNamespace(command="run", hosted_methods="audited-147-v3", hosted_deadline_epoch=time.time()+3500,
                               shard="1/5", id=[], module=[], tier=[], failfast=False, json="report.json")
        env = dict(GITHUB_ACTIONS="true", TEST_RUN_DEADLINE=str(time.time()+4500),
                   TEST_JOB_DEADLINE=str(time.time()+4500))
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "platform", "linux"):
            options = runner._hosted_options(args)
            self.assertEqual(options["cleanup_deadline_epoch"], args.hosted_deadline_epoch + 30)
            self.assertEqual((options["deadline"], options["started"]),
                             (options["work_deadline"] + 30, options["work_deadline"] - 3600))
            for cpu, expected in ((1, 1), (2, 2), (4, 4), (8, 4)):
                with (patch.object(os, "cpu_count", return_value=cpu),
                      patch.object(os, "sched_getaffinity", return_value=set(range(cpu)), create=True)):
                    self.assertEqual(runner._hosted_options(args)["capacity"], expected)
            for key, value in (("id", [ids[0]]), ("module", ["tests"]), ("tier", ["fast"]), ("failfast", True),
                ("shard", None), ("json", None), ("hosted_methods", "unknown"), ("hosted_deadline_epoch", time.time()),
                ("hosted_deadline_epoch", time.time()+3601), ("hosted_deadline_epoch", float("nan"))):
                self.assertRaises(runner.RunnerError, runner._hosted_options,
                                  SimpleNamespace(**{**vars(args), key: value}))
            for forbidden in ("STORY_WIRE_TEST_CAPTURE_DIR", "STORY_LOCAL_CI_TOKEN"):
                with patch.dict(os.environ, {forbidden: ""}):
                    self.assertRaises(runner.RunnerError, runner._hosted_options, args)
            with patch.dict(os.environ, GITHUB_ACTIONS="false"):
                self.assertRaises(runner.RunnerError, runner._hosted_options, args)
        self.assertIsNone(runner._hosted_options(SimpleNamespace(hosted_methods=None, hosted_deadline_epoch=None)))
        with tempfile.TemporaryDirectory() as directory:
            report_path = str(Path(directory) / "result.json")
            owned = runner._hosted_artifacts(report_path)
            self.assertEqual((owned.name, owned.stat().st_mode & 0o777), ("result.json.methods", 0o700))
            marker = owned / "worker.log"; marker.write_text("retained")
            self.assertRaises(FileExistsError, runner._hosted_artifacts, report_path)
            self.assertEqual(marker.read_text(), "retained")

    def test_exact_duration_boundary_passes_and_excess_fails(self) -> None:
        for total, maximum in ((1080, 540), (18000, 3600)):
            self.budget.update(max_sequential_wall_seconds=total, max_shard_wall_seconds=maximum)
            with tempfile.TemporaryDirectory() as directory:
                paths = self._reports(directory, (2, 0))
                reconciliation = suite_budget.runner.reconcile_reports(paths)
                with patch.object(suite_budget.runner, "reconcile_reports", return_value=reconciliation), patch(
                    "builtins.open", side_effect=AssertionError("budget reopened validated reports") ):
                    self.assertIs(suite_budget.check_reports(self.budget, paths), reconciliation)
                    reconciliation["shards"][0]["durations"]["wall_seconds"] = maximum + .001
                    with self.assertRaisesRegex(suite_budget.BudgetError, "shard 1/5"):
                        suite_budget.check_reports(self.budget, paths)
                    self.assertIs( suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=True), reconciliation)
                    reconciliation["shards"][0]["durations"]["wall_seconds"] = maximum
                    reconciliation["durations"]["sequential_equivalent_wall_seconds"] = total + .001
                    with self.assertRaisesRegex(suite_budget.BudgetError, "sequential"):
                        suite_budget.check_reports(self.budget, paths)
                    self.assertIs( suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=True), reconciliation)

        with tempfile.TemporaryDirectory() as directory:
            paths = self._reports(directory, (0, 0)); rows = self.report_data
            metadata = {"version": 1, "mode": "hosted-whole-method", "profile": "audited-147-v3",
                        "profile_manifest": runner._manifest(runner._HOSTED_METHOD_IDS),
                        "capacity": 2, "peak_children": 0, "work_deadline_epoch": 10000.0,
                        "cleanup_deadline_epoch": 10030.0, "launch_manifest": runner._manifest(()),
                        "launched": 0, "completed": 0, "cleanup_complete": True, "infrastructure_errors": [],
                        "timing_basis": "observed-parent-wall-and-test-sum", "scheduled_manifest": runner._manifest(())}
            for path, row in zip(paths, rows):
                row["execution"] = {**metadata, "serial_manifest": row["selection_manifest"]}
                if row["selection"]["shard"] == "5/5":
                    row["execution"].update(serial_manifest=runner._manifest(()), peak_children=1,
                        scheduled_manifest=row["selection_manifest"], launch_manifest=row["selection_manifest"],
                        launched=1, completed=1)
                Path(path).write_text(json.dumps(row))
            result = runner.reconcile_reports(paths)
            self.assertEqual(result["durations"], {"sequential_equivalent_wall_seconds": 18000,
                             "concurrent_wall_seconds": 3600, "cumulative_test_seconds": 5})
            self.assertEqual(result["execution"]["timing_basis"], "sum-observed-shard-walls")
            for key, value in (("capacity", True), ("cleanup_complete", False), ("profile", "unknown"), ("timing_basis",
                "serial_prediction"), ("extra", 1), ("completed", 1), ("cleanup_deadline_epoch", 10031),
                ("work_deadline_epoch", True), ("launch_manifest", runner._manifest((runner._HOSTED_PRIORITY_ID,)))):
                self.assertRaises(runner.RunnerError, runner._execution_metadata,
                                  {**rows[0], "execution": {**rows[0]["execution"], key: value}})
            rows[0].pop("execution"); Path(paths[0]).write_text(json.dumps(rows[0]))
            self.assertRaisesRegex(runner.RunnerError, "mixed execution", runner.reconcile_reports, paths)

    def test_subtest_expansion_and_incomplete_shards_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._reports(directory, (3, 0))
            result = runner.reconcile_reports(list(reversed(paths)))
            self.assertEqual(runner._parser().parse_args(["reconcile", *paths]).reports, paths)
            self.assertRaises(SystemExit, runner._parser().parse_args, ["reconcile", *paths[:-1]])
            self.assertEqual(tuple(row["canonical_id"] for row in result["tests"]), tuple(self.budget["subtest_caps"]))
            self.assertEqual(result["durations"], {"sequential_equivalent_wall_seconds": 1080, "concurrent_wall_seconds": 540, "cumulative_test_seconds": 5})
            self.assertEqual(suite_policy.partition_full_shards(tuple(reversed(self.budget["subtest_caps"])), self.weights), self.plan)
            self.assertEqual(self.plan.shard_ids, (("a.test",), ("b.test",), ("c.control",), ("d.control",),
                                                 (suite_policy.ISOLATED_TEST_ID,)))
            self.assertEqual(self.plan.shard_weights_microseconds, (9, 5, 3, 2, 2))
            self.assertRaisesRegex(suite_policy.SuitePolicyError, "missing the isolated test ID",
                suite_policy.partition_full_shards, tuple(self.budget["subtest_caps"])[:-1], self.weights)
            self.assertRaisesRegex(suite_policy.SuitePolicyError, "duplicate IDs", suite_policy.partition_full_shards,
                (*self.budget["subtest_caps"], suite_policy.ISOLATED_TEST_ID), self.weights)
            def write(change=lambda values: None):
                values = json.loads(json.dumps(self.report_data))
                change(values)
                for path, value in zip(paths, values): Path(path).write_text(json.dumps(value), encoding="utf-8")
            def reject(change, message):
                write(change)
                self.assertRaisesRegex(runner.RunnerError, message, runner.reconcile_reports, paths)
            def status_change(values, kind, reason):
                row = values[0]
                row["tests"][0]["status"] = "failed"
                row["totals"].update(passed=0, failures=1)
                row.update(details=[{"canonical_id": "a.test", "kind": kind, "reason": reason}], outcome="failure")
            def omit_case(values):
                for row in values: row["canonical_manifest"] = runner._manifest(("a.test", "b.test", "c.control"))
            def selection_change(values, identifier):
                values[0]["selection_manifest"] = runner._manifest((identifier,))
                values[0]["tests"][0]["canonical_id"] = identifier
            with self.assertRaisesRegex(runner.RunnerError, "reconcile requires five distinct shard report paths"):
                runner.reconcile_reports(paths[:3])
            with self.assertRaisesRegex(runner.RunnerError, "reconcile requires five distinct shard report paths"):
                runner.reconcile_reports([*paths[:3], paths[0]])
            reject(omit_case, "shard canonical inventory differs from the current frozen inventory")
            reject(lambda rows: rows[1]["selection"].update(shard="1/5"), "reports must have complementary unique five-shard identities")
            reject(lambda rows: rows[0]["selection"].update(shard="1/2"), "reports must have complementary unique five-shard identities")
            reject(lambda rows: rows[0].update(unexecuted_ids=["a.test"]), "shard 1/5 is incomplete")
            reject(lambda rows: rows[0]["environment"].update(installed_distributions_sha256="a" * 64), "shard source or execution environments disagree")
            reject( lambda rows: rows[0]["suite_policy"]["partition"].update(digest="sha256:" + "a" * 64),
                "shard canonical manifests or policy partition metadata disagree", )
            reject(lambda rows: selection_change(rows, "b.test"), "shard 1/5 differs from its ordered partition plan")
            write(lambda rows: status_change(rows, "failure", "retained failure"))
            self.assertEqual(runner.reconcile_reports(paths)["outcome"], "failure")
            with self.assertRaisesRegex(suite_budget.BudgetError, "did not all pass"):
                suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=True)
            write()
            binding = self.report_data[0]["source_binding"]
            with ( patch.object(runner, "snapshot", return_value={**binding, "digest": "sha256:" + "0" * 64}),
                self.assertRaisesRegex(runner.RunnerError, "shard source differs from the current checkout inputs"), ):
                runner.reconcile_reports(paths)
            for allowed in (False, True):
                with patch.object(suite_budget.runner, "reconcile_reports", return_value=result):
                    with self.assertRaisesRegex(suite_budget.BudgetError, "subtest scope"):
                        suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=allowed)
                with patch.object(suite_budget.runner, "reconcile_reports", side_effect=suite_budget.runner.RunnerError("incomplete")):
                    with self.assertRaisesRegex(suite_budget.runner.RunnerError, "incomplete"):
                        suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=allowed)

        with tempfile.TemporaryDirectory() as directory:
            self._reports(directory, (0, 0)); rows = self.report_data; ids = tuple(self.budget["subtest_caps"])
            canonical = runner.CanonicalCollection((), ids); first = rows[0]
            inputs = [(row, tuple(row["selection_manifest"]["ordered_ids"]), 0) for row in reversed(rows)]
            options = dict(binding=first["source_binding"], environment=first["environment"],
                           caps=self.budget["subtest_caps"], wall=3.5, metadata={}, errors=[], slowest=0)
            merged = runner._merge_method_reports(canonical, canonical, inputs, **options)
            self.assertEqual([row["canonical_id"] for row in merged["tests"]], list(ids))
            self.assertEqual(merged["durations"], {"wall_seconds": 3.5, "cumulative_test_seconds": 5})
            partial = runner._merge_method_reports(canonical, canonical, inputs[:1], **options)
            self.assertEqual((partial["outcome"], partial["totals"]["completed"],
                              len(partial["unexecuted_ids"])), ("failure", 1, 4))
            self.assertRaises(runner.RunnerError, runner._merge_method_reports,
                              canonical, canonical, inputs+inputs, **options)
            for change in (lambda r: r["tests"][0].update(subtest_count=True),
                lambda r: r["tests"][0].update(subtest_count=99), lambda r: r.update(unexecuted_ids=[ids[0]]),
                lambda r: r["tests"][0].update(canonical_id=ids[1]),
                lambda r: r["environment"].update(executable="foreign"), lambda r: r["totals"].update(errors=1),
                lambda r: r["source_binding"].update(checkout_sha="0"*40), lambda r: r.update(outcome="interrupted")):
                bad = json.loads(json.dumps(first)); change(bad)
                self.assertRaises(runner.RunnerError, runner._validate_method_report, bad, (ids[0],), ids,
                                  options["binding"], options["environment"], options["caps"], 0)
            self.assertRaises(runner.RunnerError, runner._validate_method_report, first, (ids[0],), ids,
                              options["binding"], options["environment"], options["caps"], 1)

    def test_runner_counts_successful_subtests_in_parent_record(self) -> None:
        class Synthetic(unittest.TestCase):
            def runTest(self) -> None:
                for value in (1, 2):
                    with self.subTest(value=value):
                        if value == 2: self.skipTest("synthetic platform skip")
                        self.assertGreater(value, 0)

        case = Synthetic()
        result = suite_budget.runner.TimedTextTestResult(io.StringIO(), descriptions=False, verbosity=0)
        unittest.TestSuite([case]).run(result)
        self.assertEqual(result.records([case.id()])[0]["subtest_count"], 2)


        from tests import local_ci, local_ci_coordinator as queue, local_ci_worker as worker, local_ci_state as state
        from tests.local_ci_state import LocalCIError
        from types import SimpleNamespace

        def invoke(function, error=None, message=""):
            with self.assertRaisesRegex(error, message) if error else nullcontext(): return function()

        def assert_fields(record, **expected): self.assertEqual({key: record[key] for key in expected}, expected)

        ci_identity_values = {"NAME": "Story Tests", "EMAIL": "story-tests@example.invalid"}
        caller_identity = { "GIT_AUTHOR_NAME": "Caller Author", "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Caller Committer", "GIT_COMMITTER_EMAIL": "committer@example.invalid", }
        inherited_config = { "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Inherited Git Name", }
        caller_environment = { **caller_identity, **inherited_config, "GITHUB_ACTIONS": "true", "PYTHONDONTWRITEBYTECODE": "0", "PYTHONPATH": "caller-path", }
        with patch.dict(worker.os.environ, caller_environment, clear=True):
            for ci_identity in (True, False):
                environment = worker.test_environment({"ci_identity": ci_identity})
                expected_identity = {key: ci_identity_values[key.rsplit("_", 1)[1]] for key in caller_identity} if ci_identity else caller_identity
                self.assertEqual({key: environment[key] for key in caller_identity}, expected_identity)
                expected_config = {**inherited_config, "GIT_CONFIG_COUNT": "3" if ci_identity else "1"}
                self.assertEqual({key: environment[key] for key in inherited_config}, expected_config)
                appended_config = { "GIT_CONFIG_KEY_1": "user.name", "GIT_CONFIG_VALUE_1": "Story Tests",
                    "GIT_CONFIG_KEY_2": "user.email", "GIT_CONFIG_VALUE_2": "story-tests@example.invalid", } if ci_identity else {}
                self.assertEqual({key: value for key, value in environment.items() if key.startswith("GIT_")},
                                 {**expected_identity, **expected_config, **appended_config})
                self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
                self.assertEqual(environment["PYTHONPATH"], "src")
                self.assertNotIn("GITHUB_ACTIONS", environment)
                with patch.dict(worker.os.environ, environment, clear=True):
                    self.assertEqual(worker.test_environment({"ci_identity": ci_identity}), environment)
                self.assertEqual(dict(worker.os.environ), caller_environment)

        invalid_configurations = [{"GIT_CONFIG_COUNT": count} for count in ("invalid", "-1", "01", "+1", "1.0", "")]
        invalid_configurations.extend({f"GIT_CONFIG_{field}_{index}": "orphan"} for index in (1, 2) for field in ("KEY", "VALUE"))
        for invalid_config in invalid_configurations:
            invalid_environment = {**caller_environment, **invalid_config}
            with patch.dict(worker.os.environ, invalid_environment, clear=True):
                with self.assertRaises(LocalCIError): worker.test_environment({"ci_identity": True})
                unchanged = worker.test_environment({"ci_identity": False})
                self.assertEqual({key: value for key, value in unchanged.items() if key.startswith("GIT_")},
                                 {key: value for key, value in invalid_environment.items() if key.startswith("GIT_")})
                self.assertEqual(dict(worker.os.environ), invalid_environment)

        worker_calls = Mock()
        worker_arguments = ["snapshot", "harmless-fixture"]
        worker_calls.parser.return_value.parse_args.return_value = SimpleNamespace(**dict(zip(("command", "checkout"), worker_arguments)))
        worker_calls.snapshot.return_value = {"safe": True}
        with patch.object(worker.os, "umask", worker_calls.umask), patch.object( worker.argparse, "ArgumentParser", worker_calls.parser
        ), patch.object(worker, "snapshot", worker_calls.snapshot), patch.object(worker.sys, "stdout", io.StringIO()) as output:
            self.assertEqual(worker.main(worker_arguments), 0)
            self.assertEqual(output.getvalue(), '{"safe": true}\n')
        startup = {"umask", "parser", "parser().parse_args", "snapshot"}
        self.assertEqual([row for row in worker_calls.mock_calls if row[0] in startup], [
            call.umask(0o077), call.parser(*worker_calls.parser.call_args.args, **worker_calls.parser.call_args.kwargs),
            call.parser().parse_args(worker_arguments), call.snapshot(Path("harmless-fixture")), ])

        collection = runner.CanonicalCollection((case,), (case.id(),))
        arguments = ["run", "--id", case.id()]
        with patch.object(runner, "_discover_canonical", return_value=collection), patch.object(
            runner, "_policy_select", return_value=runner.PolicySelection(collection, {})
        ), patch.object(local_ci, "runner_admission", return_value=17) as admission, patch.object(runner, "_execute") as execute:
            self.assertEqual(runner.main(arguments), 17)
            admission.assert_called_once_with(arguments)
            execute.assert_not_called()
        host_exists = Path.exists
        lima_markers = {"/run/lima-ssh-ready", "/var/run/lima-ssh-ready", "/run/lima-guestagent.pid"}
        for guest_marker, managed, hosted, expected, error in ( (False, False, "", 17, None),
            (False, True, "", None, None), (False, False, "true", None, None),
            (True, False, "", None, LocalCIError), (True, True, "", None, None), ):
            with patch.object(Path, "exists", new=lambda path: guest_marker and path == Path("/run/lima-ssh-ready") or str(path) not in lima_markers and host_exists(path)), patch.dict(
                local_ci.os.environ, {"GITHUB_ACTIONS": hosted}
            ), patch.object(local_ci, "admitted_worker", return_value=managed) as inherited, patch.object( local_ci, "submit", return_value="owned"
            ) as submit, patch.object(local_ci, "wait_job", return_value={"exit_code": 17}) as wait, patch.object(local_ci, "state_root") as state_root:
                self.assertEqual(invoke(lambda: local_ci.runner_admission(arguments), error, "submit through the host"), expected)
                inherited.assert_called_once_with()
                self.assertEqual(wait.call_args_list, [call("owned")] if expected == 17 else [])
                self.assertEqual(submit.call_count, int(expected == 17))
                if expected == 17: self.assertEqual(submit.call_args.args[1], arguments)
                state_root.assert_not_called()

        launched = []
        def launch(instance, job):
            job.update(state="running", pid=10000 + job["sequence"], process_identity="live", started_at=100.0)
            launched.append(job["id"])

        with tempfile.TemporaryDirectory(prefix="local-ci-contract-") as temporary, patch.object( queue, "snapshot", return_value={"digest": "stable"}
        ), patch.object(queue, "process_identity", return_value="live"), patch.object( queue, "group_members", return_value=[10000]
        ), patch.object(queue, "boot_identity", return_value="sysctl:100:2") as boot, patch.object(queue.Coordinator, "launch", new=launch), patch.object(queue.os, "killpg") as kill:
            root = Path(temporary)
            coordinator = queue.Coordinator(root)
            claims = [coordinator.request({ "action": "submit", "owner": {"pid": 100 + index, "identity": "live"},
                "checkout": str(root), "source": {"digest": "stable"}, "args": arguments, "python": "unused-test-python", "task": f"independent-task-{index}",
            }) for index in range(8)]
            for action in ("submit", "reserve"):
                with self.assertRaisesRegex(LocalCIError, "still owns work"):
                    coordinator.request({"action": action, "code_identity": "different-revision"})
            self.assertEqual(len(coordinator.state["jobs"]), 8)
            coordinator.tick()
            self.assertEqual(launched, [claim["id"] for claim in claims[:4]])
            self.assertEqual(len(coordinator.active()), 4)
            self.assertEqual(coordinator.request({"action": "status", "code_identity": "different-revision"})["executing_or_quarantined"], 4)
            with self.assertRaisesRegex(LocalCIError, "ownership"):
                coordinator.request({"action": "cancel", "id": claims[4]["id"], "token": "wrong"})
            self.assertEqual(coordinator.find(claims[4]["id"])["state"], "queued")
            cancelled = coordinator.request({"action": "cancel", "code_identity": "different-revision", **claims[4]})
            self.assertEqual(cancelled["state"], "cancelled")
            coordinator.find(claims[0]["id"]).update(state="finished", exit_code=0)
            coordinator.tick()
            self.assertEqual(launched, [claim["id"] for claim in claims[:4]] + [claims[5]["id"]])
            self.assertEqual(len(coordinator.active()), 4)
            starting = coordinator.find(claims[5]["id"])
            starting.update(state="starting")
            starting.pop("pid")
            for job in coordinator.active():
                job["host_boot_identity"] = "sysctl:{ sec = 100, usec = 2 } Mon Oct 5 12:00:00 KST"
            coordinator.save()
            restarted = queue.Coordinator(root)
            self.assertEqual([job["state"] for job in restarted.active()], ["quarantined"] * 4)
            restarted.tick()
            status = restarted.request({"action": "status"})
            self.assertEqual((status["capacity"], status["executing_or_quarantined"]), (4, 4))
            self.assertEqual(len(launched), 5)
            restarted.request({"action": "cancel", **claims[5]})
            restarted.tick()
            self.assertEqual(len(restarted.active()), 4)
            self.assertEqual(len(launched), 5)
            kill.assert_not_called()
            uncertain = restarted.find(claims[1]["id"])
            for code in (None, 130):
                uncertain.update(state="running", descendant_death_uncertain=False)
                with patch.object(queue, "group_members", return_value=[]), patch.object(queue, "live_owned", return_value=[]):
                    restarted.finish(uncertain, code)
                assert_fields(uncertain, state="quarantined", descendant_death_uncertain=True)
            uncertain.update(state="running", descendant_death_uncertain=False)
            with patch.object(queue, "group_members", return_value=[]), patch.object(queue, "live_owned", return_value=[12345]):
                restarted.finish(uncertain, 0)
            self.assertEqual(uncertain["state"], "quarantined")
            self.assertIn("outside the original process group", uncertain["recovery"])
            self.assertEqual(len(restarted.active()), 4)
            uncertain["owned_processes"] = {}
            child = {str(uncertain["pid"] + 1): {"pid": uncertain["pid"] + 1, "identity": "owned-child", "pgid": uncertain["pid"] + 1}}
            with patch.object(queue, "process_identity", return_value="replacement") as leader, patch.object( queue, "observed_descendants", return_value=child
            ) as observed, patch.object(queue, "group_members", return_value=[]), patch.object(
                queue, "live_owned", side_effect=lambda records: [uncertain["pid"] + 1] if records else [] ), patch.object(queue, "signal_owned") as signals:
                for identity, raced, expected_state, expected_owned in ( ("replacement", False, "quarantined", {}),
                    (None, False, "quarantined", {}), ("live", True, "quarantined", {}),
                    ("live", False, "cancelling", child), ):
                    leader.return_value = identity
                    leader.side_effect = ("live", "replacement") if raced else None
                    observed.reset_mock()
                    signals.reset_mock()
                    restarted.request({"action": "cancel", **claims[1]})
                    self.assertEqual(observed.call_args_list, [call(uncertain["pid"])] if identity == "live" else [])
                    self.assertEqual(signals.call_args_list,
                                     [call(child, queue.signal.SIGINT, outside_group=uncertain["pid"])] if expected_state == "cancelling" else [])
                    kill.assert_not_called()
                    assert_fields(uncertain, owned_processes=expected_owned, state=expected_state)
                    persisted = next(job for job in json.loads(restarted.path.read_text())["jobs"] if job["id"] == uncertain["id"])
                    self.assertEqual(persisted["owned_processes"], expected_owned)
            for index, (exit_code, replacement, grouped, owned, tracked, uncertain_before, expected) in enumerate(( (0,
                None, False, False, True, False, "finished"), (None, None, False, False, True, False, "running"), (-9,
                None, False, False, True, False, "quarantined"), (0, None, True, False, True, False, "quarantined"), (0,
                None, False, True, True, False, "quarantined"), (0, "replacement", False, False, True, False,
                "quarantined"), (None, None, False, False, False, False, "quarantined"), (0, None, False, False, True,
                True, "quarantined"), )):
                refresh_root = state.owner_directory(root / f"refresh-{index}", create=True)
                refresh = queue.Coordinator(refresh_root)
                old_owned = {"21001": {"pid": 21001, "identity": "owned-child", "pgid": 21001}} if owned else {}
                tracked_job = {"id": f"refresh-{index}", "kind": "runner", "state": "running", "executor": "native",
                    "pid": 21000, "process_identity": "live", "owner": {"pid": 123, "identity": "live"},
                    "owner_checked_at": queue.time.time(), "started_at": 100.0, "directory": str(refresh_root),
                    "checkout": str(refresh_root), "source": {"digest": "stable"}, "owned_processes": old_owned.copy(),
                    "descendant_death_uncertain": uncertain_before}
                refresh.state["jobs"].append(tracked_job)
                if tracked:
                    refresh.processes[tracked_job["id"]] = SimpleNamespace(poll=lambda: exit_code, returncode=exit_code)
                temporary_owned = {"21002": {"pid": 21002, "identity": "unverified-child", "pgid": 21002}}
                with patch.object(queue, "process_identity", side_effect=("live", replacement)), patch.object(
                    queue, "observed_descendants", return_value=temporary_owned
                ) as observed, patch.object(queue, "group_members", return_value=[21000] if grouped else []), patch.object(
                    queue, "live_owned", return_value=[21001] if owned else [] ), patch.object(queue, "signal_owned") as signals:
                    refresh.tick()
                observed.assert_called_once_with(21000)
                signals.assert_not_called()
                kill.assert_not_called()
                assert_fields(tracked_job, owned_processes=old_owned, state=expected)
                self.assertEqual(len(refresh.active()), int(expected != "finished"))
                if expected == "finished":
                    self.assertEqual((tracked_job["exit_code"], tracked_job["source_valid"]), (0, True))
                if uncertain_before: self.assertTrue(tracked_job["descendant_death_uncertain"])
            boot.return_value = "sysctl:101:2"
            rebooted = queue.Coordinator(root)
            self.assertEqual(rebooted.active(), [])
            failed = [job for job in rebooted.state["jobs"] if job["state"] == "failed"]
            self.assertEqual(len(failed), 4)
            self.assertTrue(all(job["exit_code"] == 2 and job["source_valid"] is False for job in failed))

            checkout = root / "pinned-checkout"
            checkout.mkdir()
            self.assertFalse((checkout / "tests/local_ci_worker.py").exists())
            checkout = checkout.resolve(strict=True)
            static_report = (root / "native-static-report").resolve()
            static_args = local_ci.argparse.Namespace( checkout=str(checkout), executor="native", guest_checkout=None, report_dir=str(static_report),
                base_sha="a" * 40, head_sha="b" * 40, label=[], id=[], module=[], timing_mode="shared", task="retained-native-static", )
            def campaign_arguments(report, **overrides):
                return local_ci.argparse.Namespace(**{**vars(static_args), "report_dir": str(report), **overrides})
            cached_sources = {"local_ci_worker.py": b"immutable worker source"}
            cached_bundle = root / "cached-service"
            host_state = root / "host-state"
            target_python = str(checkout / ".venv/bin/python")
            expected_environment = worker.test_environment({"ci_identity": True})
            older = campaign_arguments(root / "older-head-report")
            with patch.object(local_ci, "git", side_effect=("a" * 40, "b" * 40, "c" * 40)), patch.object( local_ci.subprocess, "run"
            ), patch.object(local_ci, "snapshot") as source:
                with self.assertRaisesRegex(LocalCIError, "HEAD must equal the pinned head SHA exactly"):
                    local_ci.campaign(older)
                source.assert_not_called()
            with patch.object(local_ci, "git", side_effect=("a" * 40, "b" * 40, "b" * 40)) as git_calls, patch.object(
                local_ci, "snapshot", return_value={"porcelain": "", "digest": "pinned-source"}
            ), patch.object(local_ci, "classify", return_value={"runtime": "false", "full": "false", "changed_paths": []}) as classify, patch.object(
                local_ci, "service_sources", return_value=("cached-code", cached_sources)
            ), patch.object(local_ci, "state_root", return_value=host_state), patch.object( local_ci, "prepare_service", return_value=cached_bundle
            ) as prepare, patch.object(local_ci.subprocess, "check_output", return_value="Python 3.11.16\n") as version, patch.object(
                local_ci.subprocess, "run", return_value=local_ci.subprocess.CompletedProcess([], 0, "static passed", "") ) as execute_static:
                self.assertEqual(local_ci.campaign(static_args), 0)
                prepare.assert_called_once_with(host_state, "cached-code", cached_sources)
                version.assert_called_once_with([target_python, "--version"], text=True)
                classify.assert_called_once_with(checkout, "a" * 40, "b" * 40, [])
                self.assertEqual(execute_static.call_args.args, ([ target_python, str(cached_bundle / "entry.py"), "static", "--checkout", str(checkout),
                    "--report", str(static_report / "static.json"), "--base", "a" * 40, "--head", "b" * 40, ],))
                self.assertEqual(execute_static.call_args.kwargs, { "cwd": checkout, "env": expected_environment, "capture_output": True, "text": True, })
                classify.return_value = {"runtime": "false", "full": "true", "changed_paths": []}
                execute_static.return_value = local_ci.subprocess.CompletedProcess([], 0, "", "")
                for timing_mode, override, concurrency in (("isolated", None, 1), ("shared", None, 4),
                                                           ("isolated", 2, 2), ("shared", 1, 1)):
                    git_calls.side_effect = ("a" * 40, "b" * 40, "b" * 40)
                    campaign_report = (root / f"campaign-{timing_mode}-{override}").resolve()
                    campaign_args = campaign_arguments(campaign_report, timing_mode=timing_mode, full_concurrency=override)
                    events = []
                    def submit_full(target, arguments, **options):
                        shard = arguments[2]
                        self.assertEqual(target, checkout)
                        self.assertEqual(arguments, ["run", "--shard", shard, "--json",
                                                     str(campaign_report / f"full-{shard[0]}.json")])
                        self.assertEqual(options, { "python": target_python, "task": static_args.task + f":full-{shard[0]}",
                            "executor": "native", "guest_checkout": None, "reservation": None, "ci_identity": True, })
                        events.append(("submit", shard))
                        return shard
                    def wait_full(identifier):
                        events.append(("wait", identifier))
                        start = ((int(identifier[0]) - 1) // concurrency) * 2
                        return {"id": identifier, "exit_code": int(identifier == "1/5"), "state": "finished",
                                "started_at": start, "finished_at": start + 1, "queue_wait_seconds": 0.25}
                    with patch.object(local_ci, "submit", side_effect=submit_full), patch.object( local_ci, "wait_job", side_effect=wait_full
                    ), patch.object( local_ci, "rpc", side_effect=lambda message: {"state": "finished", "worker_exit_code": int(message["id"] == "1/5")},
                    ), patch.object( local_ci, "control" ) as control_calls:
                        self.assertEqual(local_ci.campaign(campaign_args), 1)
                        control_calls.assert_not_called()
                    expected_events = {
                        1: [("submit", "1/5"), ("wait", "1/5"), ("submit", "2/5"), ("wait", "2/5"),
                            ("submit", "3/5"), ("wait", "3/5"), ("submit", "4/5"), ("wait", "4/5"),
                            ("submit", "5/5"), ("wait", "5/5")],
                        2: [("submit", "1/5"), ("submit", "2/5"), ("wait", "1/5"), ("wait", "2/5"),
                            ("submit", "3/5"), ("submit", "4/5"), ("wait", "3/5"), ("wait", "4/5"),
                            ("submit", "5/5"), ("wait", "5/5")],
                        3: [("submit", "1/5"), ("submit", "2/5"), ("submit", "3/5"), ("wait", "1/5"),
                            ("wait", "2/5"), ("wait", "3/5"), ("submit", "4/5"), ("submit", "5/5"),
                            ("wait", "4/5"), ("wait", "5/5")],
                        4: [("submit", "1/5"), ("submit", "2/5"), ("submit", "3/5"), ("submit", "4/5"),
                            ("wait", "1/5"), ("wait", "2/5"), ("wait", "3/5"), ("wait", "4/5"),
                            ("submit", "5/5"), ("wait", "5/5")], }[concurrency]
                    self.assertEqual(events, expected_events)
                    metadata = json.loads((campaign_report / "campaign.json").read_text())
                    self.assertEqual({key: metadata[key] for key in ("timing_mode", "full_concurrency", "exit_code", "source_valid")}, {
                        "timing_mode": timing_mode, "full_concurrency": concurrency, "exit_code": 1, "source_valid": True, })
                    starts = {1: (0, 2, 4, 6, 8), 2: (0, 0, 2, 2, 4), 3: (0, 0, 0, 2, 2),
                              4: (0, 0, 0, 0, 2)}[concurrency]
                    expected_jobs = [{"id": f"{index}/5", "exit_code": int(index == 1), "state": "finished",
                                      "started_at": start, "finished_at": start + 1, "queue_wait_seconds": 0.25} for index, start in enumerate(starts, 1)]
                    self.assertEqual(metadata["jobs"], expected_jobs)
                    self.assertEqual(json.loads((campaign_report / "full-exit-status.json").read_text()),
                                     {"full-1": 1, "full-2": 0, "full-3": 0, "full-4": 0, "full-5": 0})
                classify.return_value = {"runtime": "true", "full": "false", "changed_paths": []}
                for cleanup_case, primary_failure, body_code, interrupted, exit_code in ( ("original", True, 0, False, 2), ("release", False, 0, False, 2),
                    ("interrupt", False, 0, True, 130), ("original-interrupt", True, 0, True, 2),
                    ("status-interrupt", False, 1, True, 1), ):
                    git_calls.side_effect = ("a" * 40, "b" * 40, "b" * 40)
                    cleanup_report = (root / f"cleanup-{cleanup_case}").resolve()
                    cleanup_args = campaign_arguments(cleanup_report, timing_mode="isolated")
                    original = LocalCIError("original wait failure")
                    cleanup_error = KeyboardInterrupt() if interrupted else LocalCIError("cleanup RPC failure")
                    waits = [{"state": "reserved"}, original if primary_failure else {"exit_code": body_code}]
                    with patch.multiple( local_ci, owner=lambda: {"pid": 123, "identity": "owner"},
                        claim=lambda *args: "reservation", submit=lambda *args, **kwargs: "fast-job",
                    ), patch.object(local_ci, "wait_job", side_effect=waits), patch.object( local_ci, "control", side_effect=cleanup_error ) as cleanup:
                        if primary_failure:
                            with self.assertRaises(LocalCIError) as caught: local_ci.campaign(cleanup_args)
                            self.assertIs(caught.exception, original)
                        else: self.assertEqual(local_ci.campaign(cleanup_args), exit_code)
                    operations = [("cancel", "fast-job"), ("cancel", "reservation"), ("release", "reservation")] if primary_failure else [("release", "reservation")]
                    self.assertEqual([(call.args, call.kwargs) for call in cleanup.call_args_list], [(operation, {}) for operation in operations])
                    metadata = json.loads((cleanup_report / "campaign.json").read_text())
                    self.assertEqual(metadata["exit_code"], exit_code)
                    self.assertEqual(metadata["error"], "original wait failure" if primary_failure else
                                     "interrupted" if exit_code == 130 else "campaign cleanup failed; inspect cleanup_errors")
                    self.assertEqual(metadata["cleanup_errors"], [{"action": action, "id": identifier,
                                     "error": "interrupted" if interrupted else "cleanup RPC failure"} for action, identifier in operations])
            control = {"directory": "/tmp/durable-lima-control", "python": "/usr/bin/python3", "entry": "/tmp/durable-lima-control/control.py"}
            with self.assertRaisesRegex(LocalCIError, "control metadata missing"):
                queue.lima_command({"executor": "lima:guest", "guest_checkout": "/removed/checkout"}, "probe", "owned-job")
            prepared = {**control, "code_identity": "immutable-source"}
            with patch.object(queue, "service_sources", return_value=("immutable-source", {"local_ci_worker.py": b"sealed source"})), patch.object(
                queue.subprocess, "run", return_value=local_ci.argparse.Namespace(returncode=0, stdout=json.dumps(prepared), stderr="") ) as bootstrap:
                self.assertEqual(queue.prepare_lima_control({"executor": "lima:guest", "guest_checkout": "/initial/checkout"}), prepared)
                self.assertEqual(bootstrap.call_args.args[0], ["limactl", "shell", "--workdir", "/initial/checkout", "guest", "/initial/checkout/.venv/bin/python", "-c", queue.GUEST_BOOTSTRAP])
                package = json.loads(bootstrap.call_args.kwargs["input"])
                self.assertEqual((package["identity"], package["checkout"]), ("immutable-source", "/initial/checkout"))
                self.assertEqual(queue.base64.b64decode(package["sources"]["local_ci_worker.py"]), b"sealed source")
            for operation in ("probe", "collect", "cancel"):
                self.assertEqual(queue.lima_command({"executor": "lima:guest", "guest_checkout": "/removed/checkout", "guest_control": control}, operation, "owned-job"), [
                    "limactl", "shell", "--workdir", control["directory"], "guest", "env",
                    "PYTHONPATH=" + control["directory"], control["python"], control["entry"], operation, "owned-job", ])
            guest = state.owner_directory(root / "guest-state", create=True)
            supervisor = {"pid": 777, "process_identity": "supervisor", "boot_identity": "sysctl:200:3"}
            quarantined = {"state": "quarantined", "descendant_death_uncertain": True}
            running = {"state": "running", "descendant_death_uncertain": False}
            completed = {"state": "finished", "descendant_death_uncertain": False, "exit_code": 0, "source_valid": True}
            terminated = {"state": "terminated", "descendant_death_uncertain": False, "source_valid": False, "boot_recovery": True}
            guest_base = {"state": "running", "pid": 99999, "owned_processes": {}, "descendant_death_uncertain": False,
                          "boot_identity": "sysctl:{ sec = 200, usec = 3 } Wed Oct 7 03:00:00 UTC"}
            with patch.object(worker, "guest_job", return_value=guest), patch.object(worker, "group_members", return_value=[]), patch.object(
                worker, "live_owned", return_value=[] ), patch.object(worker, "boot_identity") as guest_boot, patch.object(worker, "process_identity") as birth:
                for record, identities, extra, reboot, finalize, expected in ( (None, (), {}, False, False,
                    quarantined), (None, (), quarantined, True, False, terminated), (supervisor, ("supervisor",
                    "supervisor"), {}, False, False, running), (supervisor, (None, None), {}, False, False,
                    quarantined), (supervisor, ("replacement", "replacement"), {}, False, False, quarantined),
                    (supervisor, ("supervisor", None), {}, False, False, quarantined), (supervisor, ("supervisor",
                    "supervisor"), quarantined, False, False, quarantined), (supervisor, (None, None), completed, False,
                    False, completed), (supervisor, (), {"descendant_death_uncertain": True}, False, True,
                    {**quarantined, "exit_code": 0, "source_valid": True}), ):
                    worker.write_json(guest / "state.json", {**guest_base, **({"supervisor": record} if record else {}), **extra})
                    birth.side_effect = identities
                    guest_boot.return_value = "sysctl:201:3" if reboot else "sysctl:200:3"
                    observed = worker.update_guest(guest / "state.json", completed) if finalize else worker.probe_guest("owned-job")
                    for record in (observed, worker.read_json(guest / "state.json")):
                        assert_fields(record, **expected)
            for replacement in ("replacement", None):
                guest_state = {"state": "running", "pid": 99999, "process_identity": "owned", "token": "capability", "owned_processes": {}}
                guest_child = {"100000": {"pid": 100000, "identity": "owned-child", "pgid": 100000}}
                worker.write_json(guest / "state.json", guest_state)
                with patch.object(worker, "guest_job", return_value=guest), patch.object(worker, "probe_guest", return_value=guest_state), patch.object(
                    worker, "process_identity", return_value="owned"
                ) as leader, patch.object(worker, "observed_descendants", return_value=guest_child) as observed, patch.object(
                    worker, "group_members", return_value=[99999]
                ), patch.object(worker, "live_owned", return_value=[100000]), patch.object(worker.os, "killpg") as guest_kill, patch.object(
                    worker.time, "monotonic", side_effect=iter(range(0, 100, 4))
                ), patch.object(worker.time, "sleep") as sleep, patch.object(worker, "signal_owned") as signals:
                    signals.side_effect = lambda *args, **kwargs: setattr(leader, "return_value", replacement)
                    invoke(lambda: worker.cancel_guest("owned-job", "capability"), LocalCIError if replacement is not None else None, "identity changed")
                    observed.assert_called_once_with(99999)
                    guest_kill.assert_called_once_with(99999, worker.signal.SIGINT)
                    actions = [worker.signal.SIGINT] if replacement is not None else [worker.signal.SIGINT, worker.signal.SIGTERM, worker.signal.SIGKILL]
                    self.assertEqual([(call.args, call.kwargs) for call in signals.call_args_list], [
                        ((guest_child, action), {"outside_group": 99999 if index == 0 else None}) for index, action in enumerate(actions)])
                    sleep.assert_not_called()
                persisted = worker.read_json(guest / "state.json")
                assert_fields(persisted, state="quarantined", descendant_death_uncertain=True, owned_processes=guest_child)
            old_boot = "sysctl:{ sec = 100, usec = 2 } Mon Oct 5 12:00:00 KST"
            same_boot = "sysctl:{ sec = 100, usec = 2 } Mon Oct 5 03:00:00 UTC"
            self.assertEqual(state.normalize_boot_identity(old_boot), "sysctl:100:2")
            self.assertFalse(state.verified_boot_change(old_boot, same_boot))
            self.assertTrue(state.verified_boot_change(old_boot, "sysctl:101:2"))
            for unsuitable in (None, "unparseable", "sysctl:100:1000000", "linux:12345678-1234-1234-1234-123456789abc"):
                self.assertFalse(state.verified_boot_change(old_boot, unsuitable))

        import os, signal, subprocess, sys, time
        events = []
        class Ordered(unittest.TestCase):
            @classmethod
            def setUpClass(cls): events.append("class start")
            @classmethod
            def tearDownClass(cls): events.append("class end")
            def runTest(self): events.append("method")
        ordered = (Ordered(), Ordered())
        runner._execute(runner.CanonicalCollection(ordered, tuple(case.id() for case in ordered)), stream=io.StringIO())
        self.assertEqual(events, ["class start", "method", "method", "class end"])
        if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true": return
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); worker_pids = []
            for index in range(2):
                work = root / str(index); work.mkdir()
                process, pipe, config = runner._launch_method(
                    [sys.executable, "-c", "pass"], work, work_deadline=time.monotonic()+15)
                try:
                    self.assertEqual(process.wait(timeout=8), 0)
                    receipt = runner._method_receipt(config, process); worker_pids.append(receipt["worker_pid"])
                    self.assertEqual((receipt["worker_exit"], receipt["reason"]), (0, "completed"))
                    for field, bad in (("reaped", False), ("group_absent", False), ("deadline", config["deadline"]+1)):
                        with patch.object(runner, "_load_json_object", return_value={**receipt, field: bad}):
                            self.assertRaises(runner.RunnerError, runner._method_receipt, config, process)
                finally:
                    os.close(pipe)
                    if process.poll() is None: process.terminate(); process.wait(timeout=8)
            grant = {**config, "work_deadline": float.fromhex("0x1.fffffffffffffp+6"), "deadline": 158.0}
            with (patch.object(runner, "_load_json_object", return_value=grant),
                  patch("ctypes.CDLL", side_effect=LookupError("admitted"))):
                self.assertRaisesRegex(LookupError, "admitted", runner._method_supervisor, work / "config.json")
                grant["deadline"] = float.fromhex("0x1.3c00000000001p+7")
                self.assertRaises(runner.RunnerError, runner._method_supervisor, work / "config.json")
            self.assertEqual(len(set(worker_pids + [os.getpid()])), 3)
            with patch("subprocess.Popen") as launch:
                self.assertRaises(runner.RunnerError, runner._launch_method, [sys.executable], root, work_deadline=0)
                launch.assert_not_called()
            work = root / "orphan"; work.mkdir(); ready = work / "ready"
            body = ("import subprocess,sys,time; from pathlib import Path; "
                    "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                    "Path(" + repr(str(ready)) + ").write_text(str(p.pid)); time.sleep(60)")
            controller = ("from tests import runner; import sys,time; from pathlib import Path; "
                          "runner._launch_method([sys.executable,'-c'," + repr(body) + "],Path(" + repr(str(work)) +
                          "),work_deadline=time.monotonic()+15); time.sleep(60)")
            parent = subprocess.Popen([sys.executable, "-c", controller], start_new_session=True)
            try:
                until = time.monotonic()+8
                while not ready.exists() and time.monotonic()<until: time.sleep(.02)
                self.assertTrue(ready.exists())
                parent.kill(); parent.wait(timeout=2)
                until = time.monotonic()+8
                while not (work/"supervisor.json").exists() and time.monotonic()<until: time.sleep(.02)
                receipt = json.loads((work/"supervisor.json").read_text())
                self.assertEqual((receipt["reason"], receipt["complete"], receipt["reaped"], receipt["group_absent"]),
                                 ("parent_closed", True, True, True))
                self.assertTrue(runner._method_group_absent(receipt["worker_pid"]))
            finally:
                if parent.poll() is None: parent.kill()
                parent.wait(timeout=2)
