"""Canonical standard-library test discovery and timing evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import stat
import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO

from tests import suite_policy


SCHEMA_VERSION = 1
RUNNER_NAME = "story-writing-agents-unittest"


class RunnerError(ValueError):
    """A collection, selection, or runner configuration error."""


@dataclass(frozen=True)
class CanonicalCollection:
    tests: tuple[unittest.TestCase, ...]
    ids: tuple[str, ...]

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(sorted({test.__class__.__module__ for test in self.tests}))


@dataclass(frozen=True)
class PolicySelection:
    selection: CanonicalCollection
    metadata: dict[str, object]


def _flatten(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise RunnerError(f"unsupported discovered test object: {item!r}")


def _canonicalize(
    suite: unittest.TestSuite, *, loader_errors: Sequence[str] = ()
) -> CanonicalCollection:
    if loader_errors:
        raise RunnerError("test collection failed:\n" + "\n".join(loader_errors))
    tests = tuple(_flatten(suite))
    if not tests:
        raise RunnerError("test collection produced no tests")
    pairs = sorted(((test.id(), test) for test in tests), key=lambda pair: pair[0])
    duplicates: list[str] = []
    previous: str | None = None
    for identifier, _test in pairs:
        if not identifier:
            raise RunnerError("discovered test has an empty canonical ID")
        if identifier == previous and (not duplicates or duplicates[-1] != identifier):
            duplicates.append(identifier)
        previous = identifier
    if duplicates:
        raise RunnerError("duplicate canonical test IDs:\n" + "\n".join(duplicates))
    return CanonicalCollection(
        tests=tuple(test for _identifier, test in pairs),
        ids=tuple(identifier for identifier, _test in pairs),
    )


def _discover_canonical(
    *,
    loader: unittest.TestLoader | None = None,
    repository_root: Path | None = None,
) -> CanonicalCollection:
    root = repository_root or Path(__file__).resolve().parents[1]
    active_loader = loader or unittest.TestLoader()
    suite = active_loader.discover(
        start_dir=str(root / "tests"),
        pattern="test*.py",
        top_level_dir=str(root),
    )
    return _canonicalize(suite, loader_errors=active_loader.errors)


def _validated_values(values: Sequence[str], option: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise RunnerError(f"{option} values must be non-empty and contain no outer whitespace")
    return tuple(dict.fromkeys(values))


def _select(
    collection: CanonicalCollection,
    *,
    ids: Sequence[str] = (),
    modules: Sequence[str] = (),
) -> CanonicalCollection:
    if not collection.tests or not collection.ids:
        raise RunnerError("test selection produced no tests")
    requested_ids = _validated_values(ids, "--id")
    requested_modules = _validated_values(modules, "--module")
    missing_ids = sorted(set(requested_ids) - set(collection.ids))
    missing_modules = sorted(set(requested_modules) - set(collection.modules))
    if missing_ids or missing_modules:
        messages = []
        if missing_ids:
            messages.append("unknown test IDs: " + ", ".join(missing_ids))
        if missing_modules:
            messages.append("unknown test modules: " + ", ".join(missing_modules))
        raise RunnerError("; ".join(messages))
    if not requested_ids and not requested_modules:
        return collection
    requested_id_set = set(requested_ids)
    requested_module_set = set(requested_modules)
    tests = tuple(
        test for test in collection.tests
        if test.id() in requested_id_set or test.__class__.__module__ in requested_module_set
    )
    if not tests:
        raise RunnerError("test selection produced no tests")
    return CanonicalCollection(tests, tuple(test.id() for test in tests))


def _policy_select(
    collection: CanonicalCollection,
    *,
    ids: Sequence[str] = (),
    modules: Sequence[str] = (),
    tiers: Sequence[str] = (),
    groups: Sequence[str] = (),
    shard: str | None = None,
    weight_path: Path = suite_policy.WEIGHT_MANIFEST_PATH,
) -> PolicySelection:
    """Compose exact selectors or derive one complete deterministic shard."""

    requested_tiers = _validated_values(tiers, "--tier")
    unknown_tiers = tuple(sorted(set(requested_tiers) - {"fast"}))
    if unknown_tiers:
        raise RunnerError("unknown test tiers: " + ", ".join(unknown_tiers))
    requested_groups = _validated_values(groups, "--group")
    if shard is not None and (ids or modules or requested_tiers or requested_groups):
        raise RunnerError("--shard cannot be combined with other selectors")
    try:
        weights = suite_policy.load_timing_weights(weight_path)
    except suite_policy.SuitePolicyError as error:
        raise RunnerError(str(error)) from error

    if shard is not None:
        if shard not in {"1/2", "2/2"}:
            raise RunnerError("--shard must be 1/2 or 2/2")
        try:
            plan = suite_policy.partition_two_shards(collection.ids, weights)
        except suite_policy.SuitePolicyError as error:
            raise RunnerError(str(error)) from error
        shard_index = int(shard[0]) - 1
        selected_ids = set(plan.shard_ids[shard_index])
    else:
        selected_ids: set[str] = set()
        has_selector = bool(ids or modules or requested_tiers or requested_groups)
        if ids or modules:
            selected_ids.update(_select(collection, ids=ids, modules=modules).ids)
        try:
            if requested_tiers:
                selected_ids.update(suite_policy.select_fast(collection.ids))
            if requested_groups:
                selected_ids.update(
                    suite_policy.select_affected_groups(
                        collection.ids, requested_groups
                    )
                )
        except suite_policy.SuitePolicyError as error:
            raise RunnerError(str(error)) from error
        if not has_selector:
            selected_ids.update(collection.ids)

    tests = tuple(test for test in collection.tests if test.id() in selected_ids)
    selection = CanonicalCollection(tests, tuple(test.id() for test in tests))
    if not selection.ids:
        raise RunnerError("test selection produced no tests")
    fast_manifest = _manifest(suite_policy.FAST_TEST_IDS)
    partition_metadata = None
    if shard is not None:
        partition_metadata = {
            "algorithm": suite_policy.SHARD_ALGORITHM,
            "digest": plan.digest,
            "group_count": len(plan.group_weights_microseconds),
            "fallback_group_count": len(plan.fallback_groups),
            "shards": [
                {
                    "identity": f"{index + 1}/2",
                    "group_count": len(plan.shard_groups[index]),
                    "predicted_weight_microseconds": plan.shard_weights_microseconds[index],
                    "selection_manifest": _manifest(plan.shard_ids[index]),
                }
                for index in range(2)
            ],
        }
    metadata: dict[str, object] = {
        "selection": {
            "tiers": list(requested_tiers),
            "groups": list(requested_groups),
            "shard": shard,
        },
        "suite_policy": {
            "version": suite_policy.POLICY_VERSION,
            "fast_tier_manifest": fast_manifest,
            "weight_manifest": {
                "schema_version": weights.version,
                "digest": weights.digest,
                "source": weights.source,
                "fallback_test_weight_microseconds": (
                    weights.fallback_test_weight_microseconds
                ),
                "provenance": weights.provenance,
            },
        },
    }
    if partition_metadata is not None:
        policy = metadata["suite_policy"]
        assert isinstance(policy, dict)
        policy["partition"] = partition_metadata
    return PolicySelection(selection, metadata)


def _manifest(ids: Sequence[str]) -> dict[str, object]:
    encoded = "".join(f"{identifier}\n" for identifier in ids).encode()
    return {
        "digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "count": len(ids),
        "ordered_ids": list(ids),
    }


def _parent_test(test: unittest.TestCase) -> unittest.TestCase:
    return getattr(test, "test_case", test)


class TimedTextTestResult(unittest.TextTestResult):
    """Preserve unittest callbacks while recording parent-test timing and status."""

    _PRIORITY = {
        "running": 0, "passed": 1, "skipped": 2, "expected_failure": 3,
        "unexpected_success": 4, "failed": 5, "error": 6, "interrupted": 7,
    }

    def __init__(self, *args: object, clock: Callable[[], float] = time.perf_counter, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._started_at: dict[str, float] = {}
        self._statuses: dict[str, str] = {}
        self._durations: dict[str, float] = {}
        self.details: list[dict[str, object]] = []

    def _identifier(self, test: unittest.TestCase) -> str:
        return _parent_test(test).id()

    def _set_status(self, test: unittest.TestCase, status: str) -> None:
        identifier = self._identifier(test)
        current = self._statuses.get(identifier, "running")
        if self._PRIORITY[status] >= self._PRIORITY[current]:
            self._statuses[identifier] = status

    def _detail(self, test: unittest.TestCase, kind: str, *, error=None, reason=None) -> None:
        parent = _parent_test(test)
        detail: dict[str, object] = {
            "canonical_id": parent.id(), "display_id": test.id(), "kind": kind,
        }
        if error is not None:
            detail["traceback"] = self._exc_info_to_string(error, test)
        if reason is not None:
            detail["reason"] = reason
        self.details.append(detail)

    def startTest(self, test):  # noqa: N802
        super().startTest(test)
        identifier = self._identifier(test)
        self._statuses[identifier] = "running"
        self._started_at[identifier] = self._clock()

    def stopTest(self, test):  # noqa: N802
        identifier = self._identifier(test)
        started = self._started_at.pop(identifier, None)
        if started is not None:
            self._durations[identifier] = max(0.0, self._clock() - started)
        super().stopTest(test)

    def addSuccess(self, test):  # noqa: N802
        super().addSuccess(test); self._set_status(test, "passed")

    def addFailure(self, test, err):  # noqa: N802
        super().addFailure(test, err); self._set_status(test, "failed")
        self._detail(test, "failure", error=err)

    def addError(self, test, err):  # noqa: N802
        super().addError(test, err); self._set_status(test, "error")
        self._detail(test, "error", error=err)

    def addSkip(self, test, reason):  # noqa: N802
        super().addSkip(test, reason); self._set_status(test, "skipped")
        self._detail(test, "skip", reason=reason)

    def addExpectedFailure(self, test, err):  # noqa: N802
        super().addExpectedFailure(test, err); self._set_status(test, "expected_failure")
        self._detail(test, "expected_failure", error=err)

    def addUnexpectedSuccess(self, test):  # noqa: N802
        super().addUnexpectedSuccess(test); self._set_status(test, "unexpected_success")
        self._detail(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):  # noqa: N802
        super().addSubTest(test, subtest, err)
        if err is not None:
            kind = "failure" if issubclass(err[0], test.failureException) else "error"
            self._set_status(test, "failed" if kind == "failure" else "error")
            self._detail(subtest, f"subtest_{kind}", error=err)

    def mark_interrupted(self) -> None:
        candidates = [key for key, status in self._statuses.items() if status == "running"]
        if not candidates:
            return
        identifier = candidates[-1]
        started = self._started_at.pop(identifier, None)
        if started is not None:
            self._durations[identifier] = max(0.0, self._clock() - started)
        self._statuses[identifier] = "interrupted"
        self.details.append({
            "canonical_id": identifier, "display_id": identifier, "kind": "interrupted",
        })

    def records(self, ordered_ids: Sequence[str]) -> list[dict[str, object]]:
        return [{
            "canonical_id": identifier,
            "status": self._statuses[identifier],
            "duration_seconds": self._durations.get(identifier, 0.0),
        } for identifier in ordered_ids if identifier in self._statuses]


@dataclass(frozen=True)
class Execution:
    result: TimedTextTestResult
    wall_seconds: float
    interrupted: bool


def _execute(selection: CanonicalCollection, *, stream: TextIO, failfast: bool = False,
             clock: Callable[[], float] = time.perf_counter) -> Execution:
    captured: list[TimedTextTestResult] = []

    def result_factory(*args, **kwargs):
        result = TimedTextTestResult(*args, clock=clock, **kwargs)
        captured.append(result)
        return result

    test_runner = unittest.TextTestRunner(
        stream=stream, verbosity=2, failfast=failfast, resultclass=result_factory,
    )
    started = clock()
    try:
        result = test_runner.run(unittest.TestSuite(selection.tests))
        return Execution(result, max(0.0, clock() - started), False)
    except KeyboardInterrupt:
        wall = max(0.0, clock() - started)
        result = captured[0]
        result.mark_interrupted()
        return Execution(result, wall, True)


def _run_report(canonical: CanonicalCollection, selection: CanonicalCollection,
                execution: Execution, *, slowest: int,
                policy_metadata: dict[str, object] | None = None) -> dict[str, object]:
    result = execution.result
    records = result.records(selection.ids)
    started_ids = {record["canonical_id"] for record in records}
    statuses = [str(record["status"]) for record in records]
    report = {
        "schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME, "kind": "run_result",
        "outcome": "interrupted" if execution.interrupted else (
            "success" if result.wasSuccessful() else "failure"
        ),
        "canonical_manifest": _manifest(canonical.ids),
        "selection_manifest": _manifest(selection.ids),
        "tests": records,
        "totals": {
            "selected": len(selection.ids), "started": len(started_ids),
            "completed": len(records) - statuses.count("interrupted"),
            "passed": statuses.count("passed"), "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "interrupted": statuses.count("interrupted"),
        },
        "unexecuted_ids": [identifier for identifier in selection.ids if identifier not in started_ids],
        "durations": {
            "wall_seconds": execution.wall_seconds,
            "cumulative_test_seconds": sum(float(row["duration_seconds"]) for row in records),
        },
        "slowest": sorted(records, key=lambda row: (
            -float(row["duration_seconds"]), str(row["canonical_id"])
        ))[:slowest],
        "details": result.details,
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "executable": sys.executable, "platform": platform.platform(),
            "machine": platform.machine(),
        },
    }
    if policy_metadata is not None:
        report.update(policy_metadata)
    return report


def _write_json(path: str, payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path == "-":
        sys.stdout.write(rendered)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_NOFOLLOW", "O_CLOEXEC"):
        flags |= int(getattr(os, name, 0))
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("JSON evidence destination is not a regular file")
        if hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise OSError("JSON evidence destination is not owned by this user")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        os.set_inheritable(descriptor, False)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            descriptor = -1
            stream.write(rendered)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_policy_human(report: dict[str, object], stream: TextIO) -> None:
    selection = report.get("selection")
    policy = report.get("suite_policy")
    if isinstance(selection, dict) and isinstance(policy, dict):
        tiers = ",".join(str(value) for value in selection.get("tiers", ())) or "-"
        groups = ",".join(str(value) for value in selection.get("groups", ())) or "-"
        print(
            "selection: "
            f"tier={tiers} groups={groups} shard={selection.get('shard') or '-'}",
            file=stream,
        )
        weights = policy.get("weight_manifest")
        partition = policy.get("partition")
        assert isinstance(weights, dict)
        partition_digest = partition.get("digest") if isinstance(partition, dict) else "-"
        print(
            "policy: "
            f"v{policy['version']} weights=v{weights['schema_version']} "
            f"weight_digest={weights['digest']} partition={partition_digest}",
            file=stream,
        )


def _write_human_report(report: dict[str, object], stream: TextIO) -> None:
    totals = report["totals"]
    durations = report["durations"]
    assert isinstance(totals, dict) and isinstance(durations, dict)
    print(
        "timing: "
        f"wall={float(durations['wall_seconds']):.3f}s "
        f"cumulative={float(durations['cumulative_test_seconds']):.3f}s "
        f"started={totals['started']}/{totals['selected']}",
        file=stream,
    )
    _write_policy_human(report, stream)
    slowest = report["slowest"]
    assert isinstance(slowest, list)
    if slowest:
        print("slowest tests:", file=stream)
    for row in slowest:
        assert isinstance(row, dict)
        print(
            f"  {float(row['duration_seconds']):.3f}s "
            f"{row['status']} {row['canonical_id']}",
            file=stream,
        )


def _load_json_object(path: str) -> dict[str, object]:
    descriptor = -1
    try:
        inspected = os.lstat(path)
        if not stat.S_ISREG(inspected.st_mode):
            raise OSError("shard report is not a regular file")
        if hasattr(os, "geteuid") and inspected.st_uid != os.geteuid():
            raise OSError("shard report is not owned by this user")
        flags = os.O_RDONLY
        for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"):
            flags |= int(getattr(os, name, 0))
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("opened shard report is not a regular file")
        if hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise OSError("opened shard report is not owned by this user")
        if (inspected.st_dev, inspected.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("shard report identity changed while opening")
        os.set_inheritable(descriptor, False)
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as stream:
            descriptor = -1
            payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunnerError(f"cannot read shard report {path!r}: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if type(payload) is not dict:
        raise RunnerError(f"shard report {path!r} must be a JSON object")
    return payload


def _required_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if type(value) is not dict:
        raise RunnerError(f"shard report has invalid {key!r} metadata")
    return value


def _required_durations(payload: dict[str, object]) -> dict[str, float]:
    durations = _required_dict(payload, "durations")
    expected_keys = {"wall_seconds", "cumulative_test_seconds"}
    if set(durations) != expected_keys:
        raise RunnerError("shard report has invalid 'durations' fields")
    validated: dict[str, float] = {}
    for key in sorted(expected_keys):
        value = durations[key]
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            normalized = -1.0
        if (
            type(value) not in (int, float)
            or not math.isfinite(normalized)
            or normalized < 0
        ):
            raise RunnerError(
                f"shard report has invalid nonnegative duration {key!r}"
            )
        validated[key] = normalized
    return validated


def _required_ids(payload: dict[str, object], key: str) -> tuple[str, ...]:
    manifest = _required_dict(payload, key)
    values = manifest.get("ordered_ids")
    if type(values) is not list or any(type(value) is not str for value in values):
        raise RunnerError(f"shard report has invalid {key!r} ordered IDs")
    identifiers = tuple(values)
    if _manifest(identifiers) != manifest:
        raise RunnerError(f"shard report has inconsistent {key!r}")
    return identifiers


def _reconcile_reports(paths: Sequence[str]) -> dict[str, object]:
    """Prove two independently emitted full shards are complementary."""

    if len(paths) != 2 or paths[0] == paths[1]:
        raise RunnerError("reconcile requires two distinct shard report paths")
    by_shard: dict[str, dict[str, object]] = {}
    durations_by_shard: dict[str, dict[str, float]] = {}
    for path in paths:
        report = _load_json_object(path)
        if report.get("runner") != RUNNER_NAME or report.get("kind") != "run_result":
            raise RunnerError(f"{path!r} is not a canonical run report")
        if report.get("outcome") not in {"success", "failure"}:
            raise RunnerError(f"{path!r} is not terminal")
        selection = _required_dict(report, "selection")
        shard = selection.get("shard")
        if shard not in {"1/2", "2/2"} or shard in by_shard:
            raise RunnerError("reports must have complementary unique shard identities")
        durations = _required_durations(report)
        totals = _required_dict(report, "totals")
        selected = totals.get("selected")
        if (
            type(selected) is not int
            or totals.get("started") != selected
            or totals.get("completed") != selected
            or report.get("unexecuted_ids") != []
        ):
            raise RunnerError(f"shard {shard} is incomplete")
        selection_ids = _required_ids(report, "selection_manifest")
        records = report.get("tests")
        if type(records) is not list or any(type(row) is not dict for row in records):
            raise RunnerError(f"shard {shard} has invalid test records")
        record_ids = tuple(row.get("canonical_id") for row in records)
        if record_ids != selection_ids:
            raise RunnerError(f"shard {shard} records do not match its selection")
        by_shard[str(shard)] = report
        durations_by_shard[str(shard)] = durations

    first, second = by_shard["1/2"], by_shard["2/2"]
    canonical_first = _required_dict(first, "canonical_manifest")
    canonical_second = _required_dict(second, "canonical_manifest")
    if canonical_first != canonical_second:
        raise RunnerError("shard canonical manifests disagree")
    canonical_ids = _required_ids(first, "canonical_manifest")
    first_policy = _required_dict(first, "suite_policy")
    second_policy = _required_dict(second, "suite_policy")
    if first_policy != second_policy:
        raise RunnerError("shard policy or partition metadata disagree")
    partition = _required_dict(first_policy, "partition")
    planned_shards = partition.get("shards")
    if type(planned_shards) is not list or len(planned_shards) != 2:
        raise RunnerError("partition metadata must describe exactly two shards")
    shard_ids = {
        identity: _required_ids(report, "selection_manifest")
        for identity, report in by_shard.items()
    }
    for index, identity in enumerate(("1/2", "2/2")):
        planned = planned_shards[index]
        if type(planned) is not dict or planned.get("identity") != identity:
            raise RunnerError("partition shard identities are invalid")
        planned_manifest = planned.get("selection_manifest")
        if planned_manifest != _manifest(shard_ids[identity]):
            raise RunnerError(f"shard {identity} differs from the partition plan")
    first_set, second_set = set(shard_ids["1/2"]), set(shard_ids["2/2"])
    if first_set & second_set:
        raise RunnerError("shard selections overlap")
    if first_set | second_set != set(canonical_ids):
        raise RunnerError("shard selections are not exhaustive")
    if tuple(identifier for identifier in canonical_ids if identifier in first_set) != shard_ids["1/2"]:
        raise RunnerError("shard 1 selection is not in canonical order")
    if tuple(identifier for identifier in canonical_ids if identifier in second_set) != shard_ids["2/2"]:
        raise RunnerError("shard 2 selection is not in canonical order")

    shard_summaries: list[dict[str, object]] = []
    for identity in ("1/2", "2/2"):
        report = by_shard[identity]
        shard_summaries.append(
            {
                "identity": identity,
                "outcome": report["outcome"],
                "selection_manifest": report["selection_manifest"],
                "totals": report["totals"],
                "durations": durations_by_shard[identity],
            }
        )
    wall_durations = tuple(
        durations_by_shard[identity]["wall_seconds"]
        for identity in ("1/2", "2/2")
    )
    cumulative_durations = tuple(
        durations_by_shard[identity]["cumulative_test_seconds"]
        for identity in ("1/2", "2/2")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "runner": RUNNER_NAME,
        "kind": "reconciliation",
        "outcome": "success"
        if all(summary["outcome"] == "success" for summary in shard_summaries)
        else "failure",
        "canonical_manifest": canonical_first,
        "suite_policy": first_policy,
        "shards": shard_summaries,
        "totals": {
            "selected": len(canonical_ids),
            "completed": sum(
                int(_required_dict(report, "totals")["completed"])
                for report in (first, second)
            ),
        },
        "durations": {
            "sequential_equivalent_wall_seconds": sum(wall_durations),
            "concurrent_wall_seconds": max(wall_durations),
            "cumulative_test_seconds": sum(cumulative_durations),
        },
        "unexecuted_ids": [],
    }


def _write_reconciliation_human(report: dict[str, object], stream: TextIO) -> None:
    canonical = _required_dict(report, "canonical_manifest")
    policy = _required_dict(report, "suite_policy")
    partition = _required_dict(policy, "partition")
    print(
        "reconciliation: "
        f"outcome={report['outcome']} count={canonical['count']} "
        f"canonical={canonical['digest']} partition={partition['digest']}",
        file=stream,
    )
    durations = _required_dict(report, "durations")
    print(
        "durations: "
        f"sequential_wall="
        f"{float(durations['sequential_equivalent_wall_seconds']):.3f}s "
        f"concurrent_wall={float(durations['concurrent_wall_seconds']):.3f}s "
        f"cumulative={float(durations['cumulative_test_seconds']):.3f}s",
        file=stream,
    )
    shards = report["shards"]
    assert isinstance(shards, list)
    for shard in shards:
        assert isinstance(shard, dict)
        totals = _required_dict(shard, "totals")
        durations = _required_dict(shard, "durations")
        print(
            f"  shard={shard['identity']} outcome={shard['outcome']} "
            f"completed={totals['completed']}/{totals['selected']} "
            f"wall={float(durations['wall_seconds']):.3f}s",
            file=stream,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run"):
        command = commands.add_parser(name)
        command.add_argument("--id", action="append", default=[])
        command.add_argument("--module", action="append", default=[])
        command.add_argument("--tier", action="append", default=[])
        command.add_argument("--group", action="append", default=[])
        command.add_argument("--shard")
        command.add_argument("--json")
        if name == "run":
            command.add_argument("--slowest", type=int, default=20)
            command.add_argument("--failfast", action="store_true")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("reports", nargs=2)
    reconcile.add_argument("--json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "reconcile":
            report = _reconcile_reports(args.reports)
            _write_reconciliation_human(report, sys.stderr)
            if args.json:
                _write_json(args.json, report)
            return 0 if report["outcome"] == "success" else 1
        if args.command == "run" and args.slowest < 0:
            raise RunnerError("--slowest must be zero or greater")
        canonical = _discover_canonical()
        selected = _policy_select(
            canonical,
            ids=args.id,
            modules=args.module,
            tiers=args.tier,
            groups=args.group,
            shard=args.shard,
        )
        selection = selected.selection
        if args.command == "list":
            payload = {
                "schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME, "kind": "manifest",
                "canonical_manifest": _manifest(canonical.ids),
                "selection_manifest": _manifest(selection.ids),
            }
            payload.update(selected.metadata)
            if args.json:
                _write_json(args.json, payload)
            else:
                print(*selection.ids, sep="\n")
                manifest = payload["selection_manifest"]
                assert isinstance(manifest, dict)
                print(f"selected: {manifest['count']}")
                print(f"digest: {manifest['digest']}")
                _write_policy_human(selected.metadata, sys.stdout)
            return 0
        execution = _execute(selection, stream=sys.stderr, failfast=args.failfast)
        report = _run_report(
            canonical,
            selection,
            execution,
            slowest=args.slowest,
            policy_metadata=selected.metadata,
        )
        _write_human_report(report, sys.stderr)
        if args.json:
            _write_json(args.json, report)
        if execution.interrupted:
            return 130
        return 0 if execution.result.wasSuccessful() else 1
    except RunnerError as error:
        print(f"test runner configuration error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"test runner evidence error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
