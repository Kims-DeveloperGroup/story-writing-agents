"""Focused contracts for explicit lanes and deterministic two-shard policy."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import runner, suite_policy


def _case_type(name: str, *, module: str = "synthetic.alpha", **methods):
    return type(name, (unittest.TestCase,), {"__module__": module, **methods})


def _collection(*tests: unittest.TestCase) -> runner.CanonicalCollection:
    ordered = tuple(sorted(tests, key=lambda test: test.id()))
    return runner.CanonicalCollection(ordered, tuple(test.id() for test in ordered))


def _weights(**groups: int) -> suite_policy.TimingWeights:
    return suite_policy.TimingWeights(
        version=1,
        fallback_test_weight_microseconds=3,
        group_weights_microseconds=groups,
        provenance="synthetic deterministic timing seed",
        digest="sha256:synthetic",
        source="synthetic.json",
    )


def _passing_collection() -> runner.CanonicalCollection:
    Alpha = _case_type(
        "Alpha", test_a=lambda self: None, test_b=lambda self: None
    )
    Beta = _case_type(
        "Beta", module="synthetic.beta", test_a=lambda self: None
    )
    Gamma = _case_type(
        "Gamma", module="synthetic.gamma", test_a=lambda self: None
    )
    return _collection(
        Alpha("test_a"), Alpha("test_b"), Beta("test_a"), Gamma("test_a")
    )


def _write_weights(path: Path, *, groups: dict[str, int] | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "fallback_test_weight_microseconds": 3,
                "group_weights_microseconds": groups or {},
                "policy_version": 1,
                "provenance": "synthetic deterministic timing seed",
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )


class LanePolicyTests(unittest.TestCase):
    def test_repository_groups_keep_the_slow_storage_split_explicit(self):
        self.assertEqual(
            set(suite_policy.AFFECTED_GROUPS),
            {
                "artifact-storage",
                "database",
                "git-storage",
                "interactive-terminal",
                "provider-process",
                "runner",
                "session-runtime",
                "workspace-core",
                "workspace-session",
            },
        )
        git_modules = {
            selector.module
            for selector in suite_policy.AFFECTED_GROUPS["git-storage"]
        }
        workspace_session_modules = {
            selector.module
            for selector in suite_policy.AFFECTED_GROUPS["workspace-session"]
        }
        session_runtime_modules = {
            selector.module
            for selector in suite_policy.AFFECTED_GROUPS["session-runtime"]
        }
        database_modules = {
            selector.module
            for selector in suite_policy.AFFECTED_GROUPS["database"]
        }
        workspace_modules = {
            selector.module
            for selector in suite_policy.AFFECTED_GROUPS["workspace-core"]
        }
        artifact_modules = {
            selector.module
            for selector in suite_policy.AFFECTED_GROUPS["artifact-storage"]
        }
        self.assertFalse(git_modules & database_modules)
        self.assertFalse(database_modules & workspace_modules)
        self.assertFalse(database_modules & artifact_modules)
        self.assertFalse(workspace_modules & artifact_modules)
        self.assertFalse(git_modules & workspace_session_modules)
        self.assertFalse(git_modules & session_runtime_modules)
        self.assertFalse(workspace_session_modules & session_runtime_modules)
        self.assertFalse(database_modules & workspace_session_modules)
        self.assertEqual(
            git_modules,
            {
                "tests.test_git_adapter_integration",
                "tests.test_git_adapter_process",
                "tests.test_git_contracts",
                "tests.test_git_external_operation_inspection",
                "tests.test_git_linked_worktree_reads",
                "tests.test_git_process",
                "tests.test_git_process_profiles",
                "tests.test_git_repository_lock",
                "tests.test_git_session_checkpoint_classifier",
                "tests.test_git_session_checkpoint_contracts",
                "tests.test_git_session_checkpoint_process",
                "tests.test_git_session_checkpoints",
                "tests.test_git_session_contracts",
                "tests.test_git_session_history",
                "tests.test_git_session_history_contracts",
                "tests.test_git_session_resources",
                "tests.test_git_story_commit_candidates",
                "tests.test_git_story_commit_contracts",
                "tests.test_git_story_commit_process",
                "tests.test_git_story_commits",
            },
        )
        self.assertIn("tests.test_git_session_checkpoints", git_modules)
        self.assertIn("tests.test_session_lifecycle", session_runtime_modules)
        self.assertEqual(
            workspace_session_modules,
            {
                "tests.test_workspace_session_history",
                "tests.test_workspace_session_resources",
            },
        )
        self.assertEqual(
            database_modules,
            {
                "tests.test_sqlite_collaboration_event_store",
                "tests.test_sqlite_conversation_storage",
                "tests.test_sqlite_database",
            },
        )
        self.assertEqual(
            workspace_modules,
            {
                "tests.test_workspace_and_config",
                "tests.test_workspace_fixtures",
                "tests.test_workspace_v2_initialization",
            },
        )
        self.assertEqual(artifact_modules, {"tests.test_writing_artifacts"})

    def test_fast_lane_is_exact_canonical_order_and_missing_ids_fail(self):
        canonical = ("z.Case.test_z", "a.Case.test_a", "m.Case.test_m")
        with mock.patch.object(
            suite_policy, "FAST_TEST_IDS", (canonical[2], canonical[0])
        ):
            self.assertEqual(
                suite_policy.select_fast(canonical), (canonical[0], canonical[2])
            )
            with self.assertRaisesRegex(
                suite_policy.SuitePolicyError, "missing canonical IDs"
            ):
                suite_policy.select_fast(canonical[:2])

    def test_named_groups_form_union_and_reject_unknown_or_empty_selectors(self):
        canonical = (
            "tests.alpha.First.test_one",
            "tests.alpha.Second.test_two",
            "tests.beta.Only.test_three",
        )
        groups = {
            "alpha": (suite_policy.GroupSelector("tests.alpha"),),
            "one": (suite_policy.GroupSelector("tests.alpha", "First"),),
            "empty": (suite_policy.GroupSelector("tests.missing"),),
        }
        with mock.patch.object(suite_policy, "AFFECTED_GROUPS", groups):
            self.assertEqual(
                suite_policy.select_affected_groups(canonical, ("one", "alpha")),
                canonical[:2],
            )
            with self.assertRaisesRegex(
                suite_policy.SuitePolicyError, "unknown affected groups"
            ):
                suite_policy.select_affected_groups(canonical, ("unknown",))
            with self.assertRaisesRegex(
                suite_policy.SuitePolicyError, "empty selector"
            ):
                suite_policy.select_affected_groups(canonical, ("empty",))

    def test_shard_selector_rejects_other_selectors_and_invalid_identity(self):
        collection = _passing_collection()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "weights.json"
            _write_weights(path)
            with self.assertRaisesRegex(runner.RunnerError, "cannot be combined"):
                runner._policy_select(
                    collection,
                    ids=(collection.ids[0],),
                    shard="1/2",
                    weight_path=path,
                )
            with self.assertRaisesRegex(runner.RunnerError, "must be 1/2 or 2/2"):
                runner._policy_select(collection, shard="3/2", weight_path=path)


class TimingWeightTests(unittest.TestCase):
    def test_manifest_is_strict_versioned_positive_and_content_digested(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "weights.json"
            _write_weights(path, groups={"synthetic.alpha.Alpha": 11})
            loaded = suite_policy.load_timing_weights(path)
            self.assertEqual(loaded.version, 1)
            self.assertEqual(loaded.fallback_test_weight_microseconds, 3)
            self.assertEqual(
                loaded.group_weights_microseconds, {"synthetic.alpha.Alpha": 11}
            )
            self.assertTrue(loaded.digest.startswith("sha256:"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            for field, invalid in (
                ("schema_version", 2),
                ("policy_version", 2),
                ("fallback_test_weight_microseconds", 0),
                ("provenance", ""),
                ("group_weights_microseconds", {"synthetic.alpha.Alpha": True}),
            ):
                with self.subTest(field=field):
                    broken = dict(payload)
                    broken[field] = invalid
                    path.write_text(json.dumps(broken), encoding="utf-8")
                    with self.assertRaises(suite_policy.SuitePolicyError):
                        suite_policy.load_timing_weights(path)

    def test_lpt_is_deterministic_class_atomic_balanced_and_uses_fallback(self):
        collection = _passing_collection()
        weights = _weights(
            **{
                "synthetic.alpha.Alpha": 10,
                "synthetic.beta.Beta": 10,
            }
        )
        first = suite_policy.partition_two_shards(collection.ids, weights)
        second = suite_policy.partition_two_shards(collection.ids, weights)
        self.assertEqual(first, second)
        self.assertEqual(first.shard_weights_microseconds, (13, 10))
        self.assertEqual(first.fallback_groups, ("synthetic.gamma.Gamma",))
        alpha_ids = {
            identifier for identifier in collection.ids if ".Alpha." in identifier
        }
        self.assertTrue(
            alpha_ids <= set(first.shard_ids[0])
            or alpha_ids <= set(first.shard_ids[1])
        )
        self.assertFalse(set(first.shard_ids[0]) & set(first.shard_ids[1]))
        self.assertEqual(
            set(first.shard_ids[0]) | set(first.shard_ids[1]), set(collection.ids)
        )

    def test_stale_weight_group_is_fatal(self):
        collection = _passing_collection()
        with self.assertRaisesRegex(suite_policy.SuitePolicyError, "stale groups"):
            suite_policy.partition_two_shards(
                collection.ids, _weights(**{"removed.module.Case": 5})
            )


class ShardRunnerTests(unittest.TestCase):
    def _reports(self, directory: Path) -> tuple[Path, Path]:
        collection = _passing_collection()
        weight_path = directory / "weights.json"
        _write_weights(
            weight_path,
            groups={
                "synthetic.alpha.Alpha": 10,
                "synthetic.beta.Beta": 10,
            },
        )
        paths: list[Path] = []
        for identity in ("1/2", "2/2"):
            selected = runner._policy_select(
                collection, shard=identity, weight_path=weight_path
            )
            execution = runner._execute(selected.selection, stream=io.StringIO())
            report = runner._run_report(
                collection,
                selected.selection,
                execution,
                slowest=2,
                policy_metadata=selected.metadata,
            )
            path = directory / ("shard-" + identity[0] + ".json")
            path.write_text(json.dumps(report), encoding="utf-8")
            paths.append(path)
        return paths[0], paths[1]

    def test_two_shards_independently_derive_identical_complementary_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            first_path, second_path = self._reports(Path(temporary))
            first = json.loads(first_path.read_text(encoding="utf-8"))
            second = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertEqual(first["canonical_manifest"], second["canonical_manifest"])
        self.assertEqual(first["suite_policy"], second["suite_policy"])
        first_ids = set(first["selection_manifest"]["ordered_ids"])
        second_ids = set(second["selection_manifest"]["ordered_ids"])
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(
            first_ids | second_ids,
            set(first["canonical_manifest"]["ordered_ids"]),
        )

    def test_reconcile_cli_proves_terminal_disjoint_exhaustive_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first_path, second_path = self._reports(directory)
            first = json.loads(first_path.read_text(encoding="utf-8"))
            second = json.loads(second_path.read_text(encoding="utf-8"))
            output = directory / "reconciled.json"
            with redirect_stderr(io.StringIO()):
                code = runner.main(
                    (
                        "reconcile",
                        str(second_path),
                        str(first_path),
                        "--json",
                        str(output),
                    )
                )
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(report["outcome"], "success")
        self.assertEqual(report["totals"]["selected"], 4)
        self.assertEqual(report["totals"]["completed"], 4)
        self.assertAlmostEqual(
            report["durations"]["sequential_equivalent_wall_seconds"],
            first["durations"]["wall_seconds"]
            + second["durations"]["wall_seconds"],
        )
        self.assertAlmostEqual(
            report["durations"]["concurrent_wall_seconds"],
            max(
                first["durations"]["wall_seconds"],
                second["durations"]["wall_seconds"],
            ),
        )
        self.assertAlmostEqual(
            report["durations"]["cumulative_test_seconds"],
            first["durations"]["cumulative_test_seconds"]
            + second["durations"]["cumulative_test_seconds"],
        )
        self.assertEqual(report["unexecuted_ids"], [])

    def test_reconcile_inputs_reject_unsafe_types_without_blocking_or_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first_path, second_path = self._reports(directory)
            outside = directory / "outside.json"
            outside.write_bytes(first_path.read_bytes())
            symlink = directory / "symlink.json"
            symlink.symlink_to(outside)
            existing_directory = directory / "directory.json"
            existing_directory.mkdir()
            unsafe = [symlink, existing_directory]
            fifo = directory / "fifo.json"
            if hasattr(os, "mkfifo"):
                os.mkfifo(fifo)
                unsafe.append(fifo)
            if Path("/dev/null").exists():
                unsafe.append(Path("/dev/null"))
            for path in unsafe:
                with self.subTest(path=str(path)), redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(
                        runner.main(("reconcile", str(path), str(second_path))),
                        2,
                    )
                    self.assertIn("cannot read shard report", errors.getvalue())
            self.assertEqual(outside.read_bytes(), first_path.read_bytes())
            self.assertTrue(symlink.is_symlink())
            self.assertTrue(existing_directory.is_dir())
            if fifo in unsafe:
                self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_reconcile_inputs_reject_foreign_owner_and_name_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            first_path, _second_path = self._reports(Path(temporary))
            inspected = first_path.lstat()
            if hasattr(os, "geteuid"):
                foreign_uid = inspected.st_uid + 1
                with mock.patch.object(runner.os, "geteuid", return_value=foreign_uid):
                    with self.assertRaisesRegex(runner.RunnerError, "not owned"):
                        runner._load_json_object(str(first_path))
            swapped = SimpleNamespace(
                st_mode=inspected.st_mode,
                st_uid=inspected.st_uid,
                st_dev=inspected.st_dev,
                st_ino=inspected.st_ino + 1,
            )
            with mock.patch.object(runner.os, "fstat", return_value=swapped):
                with self.assertRaisesRegex(runner.RunnerError, "identity changed"):
                    runner._load_json_object(str(first_path))

    def test_reconcile_rejects_overlap_incompletion_and_plan_disagreement(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first_path, second_path = self._reports(directory)
            original = json.loads(second_path.read_text(encoding="utf-8"))
            mutations = (
                (
                    "duplicate-shard",
                    lambda report: report["selection"].update(shard="1/2"),
                ),
                (
                    "incomplete",
                    lambda report: report["unexecuted_ids"].append(
                        "missing.Case.test"
                    ),
                ),
                (
                    "plan-disagreement",
                    lambda report: report["suite_policy"]["partition"].update(
                        digest="sha256:different"
                    ),
                ),
                (
                    "duration-not-dict",
                    lambda report: report.update(durations=[]),
                ),
                (
                    "duration-fields",
                    lambda report: report["durations"].update(extra_seconds=0),
                ),
                (
                    "negative-wall",
                    lambda report: report["durations"].update(wall_seconds=-1),
                ),
                (
                    "boolean-cumulative",
                    lambda report: report["durations"].update(
                        cumulative_test_seconds=True
                    ),
                ),
            )
            for name, mutate in mutations:
                with self.subTest(name=name):
                    broken = json.loads(json.dumps(original))
                    mutate(broken)
                    second_path.write_text(json.dumps(broken), encoding="utf-8")
                    with redirect_stderr(io.StringIO()):
                        self.assertEqual(
                            runner.main(
                                ("reconcile", str(first_path), str(second_path))
                            ),
                            2,
                        )


if __name__ == "__main__":
    unittest.main()
