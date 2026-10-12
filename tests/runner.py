"""Canonical standard-library test discovery and timing evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
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
from tests.local_ci_state import LocalCIError, snapshot


SCHEMA_VERSION = 2
RUNNER_NAME = "story-writing-agents-unittest"
CANONICAL_INVENTORY_PATH = Path(__file__).parent / "fixtures" / "test_budget_v1.json"


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


def _suite_policy_metadata(weights: suite_policy.TimingWeights,
                           plan: suite_policy.ShardPlan | None = None) -> dict[str, object]:
    policy: dict[str, object] = {
        "version": suite_policy.POLICY_VERSION,
        "fast_tier_manifest": _manifest(suite_policy.FAST_TEST_IDS),
        "weight_manifest": {
            "schema_version": weights.version, "digest": weights.digest,
            "source": weights.source,
            "fallback_test_weight_microseconds": weights.fallback_test_weight_microseconds,
            "provenance": weights.provenance,
        },
    }
    if plan is not None:
        policy["partition"] = {
            "algorithm": suite_policy.SHARD_ALGORITHM, "shard_count": suite_policy.SHARD_COUNT,
            "digest": plan.digest, "group_count": len(plan.group_weights_microseconds),
            "fallback_group_count": len(plan.fallback_groups),
            "shards": [
                {"identity": f"{index + 1}/4", "group_count": len(plan.shard_groups[index]),
                 "predicted_weight_microseconds": plan.shard_weights_microseconds[index],
                 "selection_manifest": _manifest(plan.shard_ids[index])}
                for index in range(suite_policy.SHARD_COUNT)
            ],
        }
    return policy


def _policy_select(
    collection: CanonicalCollection,
    *,
    ids: Sequence[str] = (),
    modules: Sequence[str] = (),
    tiers: Sequence[str] = (),
    shard: str | None = None,
    weight_path: Path = suite_policy.WEIGHT_MANIFEST_PATH,
) -> PolicySelection:
    """Compose exact selectors or derive one complete deterministic shard."""

    requested_tiers = _validated_values(tiers, "--tier")
    unknown_tiers = tuple(sorted(set(requested_tiers) - {"fast"}))
    if unknown_tiers:
        raise RunnerError("unknown test tiers: " + ", ".join(unknown_tiers))
    if shard is not None and (ids or modules or requested_tiers):
        raise RunnerError("--shard cannot be combined with other selectors")
    try:
        weights = suite_policy.load_timing_weights(weight_path)
    except suite_policy.SuitePolicyError as error:
        raise RunnerError(str(error)) from error

    if shard is not None:
        if shard not in {f"{index}/4" for index in range(1, 5)}:
            raise RunnerError("--shard must be 1/4, 2/4, 3/4, or 4/4")
        try:
            plan = suite_policy.partition_full_shards(collection.ids, weights)
        except suite_policy.SuitePolicyError as error:
            raise RunnerError(str(error)) from error
        shard_index = int(shard[0]) - 1
        selected_ids = set(plan.shard_ids[shard_index])
    else:
        selected_ids: set[str] = set()
        has_selector = bool(ids or modules or requested_tiers)
        if ids or modules:
            selected_ids.update(_select(collection, ids=ids, modules=modules).ids)
        try:
            if requested_tiers:
                selected_ids.update(suite_policy.select_fast(collection.ids))
        except suite_policy.SuitePolicyError as error:
            raise RunnerError(str(error)) from error
        if not has_selector:
            selected_ids.update(collection.ids)

    tests = tuple(test for test in collection.tests if test.id() in selected_ids)
    selection = CanonicalCollection(tests, tuple(test.id() for test in tests))
    if not selection.ids:
        raise RunnerError("test selection produced no tests")
    metadata = {
        "selection": {"tiers": list(requested_tiers), "shard": shard},
        "suite_policy": _suite_policy_metadata(weights, plan if shard is not None else None),
    }
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
        self._subtests: dict[str, int] = {}
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
        if _parent_test(test) is not test:
            identifier = self._identifier(test)
            self._subtests[identifier] = self._subtests.get(identifier, 0) + 1
        self._detail(test, "skip", reason=reason)

    def addExpectedFailure(self, test, err):  # noqa: N802
        super().addExpectedFailure(test, err); self._set_status(test, "expected_failure")
        self._detail(test, "expected_failure", error=err)

    def addUnexpectedSuccess(self, test):  # noqa: N802
        super().addUnexpectedSuccess(test); self._set_status(test, "unexpected_success")
        self._detail(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):  # noqa: N802
        super().addSubTest(test, subtest, err)
        identifier = self._identifier(test)
        self._subtests[identifier] = self._subtests.get(identifier, 0) + 1
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
            "subtest_count": self._subtests.get(identifier, 0),
        } for identifier in ordered_ids if identifier in self._statuses]


def _source_snapshot() -> dict[str, object]:
    try:
        return snapshot(Path(__file__).resolve().parents[1])
    except LocalCIError as error:
        raise RunnerError(f"cannot capture test source: {error}") from error


def _execution_environment() -> dict[str, str]:
    distributions = sorted((str(item.metadata.get("Name", "")).lower(), item.version)
                           for item in importlib.metadata.distributions())
    encoded = json.dumps(distributions, separators=(",", ":")).encode()
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "executable": sys.executable, "platform": platform.platform(),
        "system": platform.system(), "machine": platform.machine(),
        "dependency_lock_sha256": hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "uv.lock").read_bytes()).hexdigest(),
        "installed_distributions_sha256": hashlib.sha256(encoded).hexdigest(),
    }


@dataclass(frozen=True)
class Execution:
    result: TimedTextTestResult
    wall_seconds: float
    interrupted: bool
    source_binding: dict[str, object]
    environment: dict[str, str]
    environment_valid: bool


def _execute(selection: CanonicalCollection, *, stream: TextIO, failfast: bool = False,
             clock: Callable[[], float] = time.perf_counter) -> Execution:
    before = _source_snapshot()
    environment = _execution_environment()
    captured: list[TimedTextTestResult] = []
    def result_factory(*args, **kwargs):
        result = TimedTextTestResult(*args, clock=clock, **kwargs)
        captured.append(result)
        return result
    test_runner = unittest.TextTestRunner(
        verbosity=2, failfast=failfast, resultclass=result_factory,
    )
    started = clock()
    interrupted = False
    try:
        result = test_runner.run(unittest.TestSuite(selection.tests))
    except KeyboardInterrupt:
        interrupted = True
        result = captured[0]
        result.mark_interrupted()
    wall = max(0.0, clock() - started)
    after = _source_snapshot()
    binding = {
        "checkout_sha": before["checkout_sha"], "checkout_tree": before["checkout_tree"],
        "before_digest": before["digest"], "after_digest": after["digest"],
        "source_valid": before == after,
    }
    return Execution(result, wall, interrupted, binding, environment,
                     environment == _execution_environment())


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
            "success" if result.wasSuccessful() and execution.source_binding["source_valid"]
                and execution.environment_valid else "failure"
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
        "source_binding": execution.source_binding,
        "environment": execution.environment,
        "environment_valid": execution.environment_valid,
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
        print(
            "selection: "
            f"tier={tiers} shard={selection.get('shard') or '-'}",
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
    ids = manifest.get("ordered_ids")
    if (type(manifest.get("count")) is not int or type(manifest.get("digest")) is not str
            or type(ids) is not list or not ids or any(type(item) is not str or not item for item in ids)
            or len(set(ids)) != len(ids) or manifest != _manifest(ids)):
        raise RunnerError(f"shard {key} is invalid")
    return tuple(ids)


def _required_report_identity(report: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    source = _required_dict(report, "source_binding")
    keys = {"checkout_sha", "checkout_tree", "before_digest", "after_digest", "source_valid"}
    if set(source) != keys or source["source_valid"] is not True:
        raise RunnerError("shard source binding is invalid or stale")
    for key in ("checkout_sha", "checkout_tree"):
        value = source[key]
        if type(value) is not str or len(value) != 40 or set(value) - set("0123456789abcdef"):
            raise RunnerError("shard source commit/tree is invalid")
    digest = source["before_digest"]
    if (type(digest) is not str or not digest.startswith("sha256:") or len(digest) != 71
            or set(digest[7:]) - set("0123456789abcdef") or source["after_digest"] != digest):
        raise RunnerError("shard source changed during execution")
    environment = _required_dict(report, "environment")
    common = ("python_version", "python_implementation", "system", "machine",
              "dependency_lock_sha256", "installed_distributions_sha256")
    if (set(environment) != set(common) | {"executable", "platform"}
            or any(type(value) is not str or not value for value in environment.values())
            or report.get("environment_valid") is not True):
        raise RunnerError("shard execution environment is invalid or changed")
    for key in ("dependency_lock_sha256", "installed_distributions_sha256"):
        value = environment[key]
        if len(value) != 64 or set(value) - set("0123456789abcdef"):
            raise RunnerError("shard environment fingerprint is invalid")
    return source, {key: environment[key] for key in common}


def reconcile_reports(paths: Sequence[str]) -> dict[str, object]:
    """Validate the complete current four-report contract and retain its records."""
    identities = tuple(f"{index}/4" for index in range(1, 5))
    if len(paths) != 4 or len(set(paths)) != 4:
        raise RunnerError("reconcile requires four distinct shard report paths")
    by_shard: dict[str, dict[str, object]] = {}
    durations_by_shard: dict[str, dict[str, float]] = {}
    bindings = []
    allowed = {"passed", "skipped", "failed", "error", "expected_failure", "unexpected_success"}
    counters = ("selected", "started", "completed", "passed", "failures", "errors", "skipped",
                "expected_failures", "unexpected_successes", "interrupted")
    for path in paths:
        report = _load_json_object(path)
        if (type(report.get("schema_version")) is not int or report.get("schema_version") != SCHEMA_VERSION
                or report.get("runner") != RUNNER_NAME
                or report.get("kind") != "run_result" or report.get("outcome") not in {"success", "failure"}):
            raise RunnerError(f"{path!r} is not a current terminal canonical run report")
        selection = _required_dict(report, "selection")
        shard = selection.get("shard")
        if shard not in identities or shard in by_shard or selection.get("tiers") != []:
            raise RunnerError("reports must have complementary unique four-shard identities")
        bindings.append(_required_report_identity(report))
        durations = _required_durations(report)
        totals = _required_dict(report, "totals")
        if any(type(totals.get(key)) is not int or totals[key] < 0 for key in counters):
            raise RunnerError(f"shard {shard} has invalid totals")
        selected = totals["selected"]
        if (selected <= 0 or totals["started"] != selected or totals["completed"] != selected
                or totals["interrupted"] != 0 or report.get("unexecuted_ids") != []):
            raise RunnerError(f"shard {shard} is incomplete")
        selection_ids = _required_ids(report, "selection_manifest")
        records = report.get("tests")
        if type(records) is not list or any(type(row) is not dict for row in records):
            raise RunnerError(f"shard {shard} has invalid test records")
        if len(records) != selected or tuple(row.get("canonical_id") for row in records) != selection_ids:
            raise RunnerError(f"shard {shard} records do not match its selection")
        for row in records:
            duration = row.get("duration_seconds")
            count = row.get("subtest_count")
            if (row.get("status") not in allowed or type(count) is not int or count < 0
                    or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
                raise RunnerError(f"shard {shard} has an invalid terminal test record")
        statuses = [row["status"] for row in records]
        if totals["passed"] != statuses.count("passed"):
            raise RunnerError(f"shard {shard} pass totals disagree with records")
        for status, key in (("skipped", "skipped"), ("failed", "failures"), ("error", "errors"),
                            ("expected_failure", "expected_failures"), ("unexpected_success", "unexpected_successes")):
            if totals[key] < statuses.count(status):
                raise RunnerError(f"shard {shard} outcome totals disagree with records")
        details = report.get("details")
        if type(details) is not list or any(type(item) is not dict for item in details):
            raise RunnerError(f"shard {shard} details are invalid")
        if any(item.get("canonical_id") not in selection_ids for item in details):
            raise RunnerError(f"shard {shard} details refer to unselected tests")
        for key, kinds in (("failures", {"failure", "subtest_failure"}),
                           ("errors", {"error", "subtest_error"}), ("skipped", {"skip"}),
                           ("expected_failures", {"expected_failure"}),
                           ("unexpected_successes", {"unexpected_success"})):
            if totals[key] != sum(item.get("kind") in kinds for item in details):
                raise RunnerError(f"shard {shard} totals disagree with outcome details")
        if not math.isclose(durations["cumulative_test_seconds"],
                            sum(row["duration_seconds"] for row in records), rel_tol=1e-12, abs_tol=1e-9):
            raise RunnerError(f"shard {shard} cumulative duration disagrees with records")
        for row in records:
            if row["status"] == "skipped" and not any(
                item.get("canonical_id") == row["canonical_id"] and item.get("kind") == "skip"
                and type(item.get("reason")) is str and item["reason"] for item in details
            ):
                raise RunnerError(f"shard {shard} skipped record has no attributable reason")
        successful = (all(status in {"passed", "skipped"} for status in statuses)
                      and not any(totals[key] for key in ("failures", "errors", "expected_failures", "unexpected_successes")))
        if (report["outcome"] == "success") != successful:
            raise RunnerError(f"shard {shard} outcome disagrees with records")
        by_shard[shard] = report
        durations_by_shard[shard] = durations
    if any(binding != bindings[0] for binding in bindings[1:]):
        raise RunnerError("shard source or execution environments disagree")
    first = by_shard[identities[0]]
    canonical = _required_dict(first, "canonical_manifest")
    canonical_ids = _required_ids(first, "canonical_manifest")
    inventory = _load_json_object(str(CANONICAL_INVENTORY_PATH)).get("subtest_caps")
    if (type(inventory) is not dict or not inventory
            or any(type(identifier) is not str or not identifier
                   or type(cap) is not int or cap < 0
                   for identifier, cap in inventory.items())):
        raise RunnerError("current frozen canonical inventory is invalid")
    if canonical_ids != tuple(sorted(inventory)):
        raise RunnerError("shard canonical inventory differs from the current frozen inventory")
    policy = _required_dict(first, "suite_policy")
    if any(_required_dict(report, "canonical_manifest") != canonical
           or _required_dict(report, "suite_policy") != policy for report in by_shard.values()):
        raise RunnerError("shard canonical manifests or policy partition metadata disagree")
    try:
        weights = suite_policy.load_timing_weights()
        plan = suite_policy.partition_full_shards(canonical_ids, weights)
    except suite_policy.SuitePolicyError as error:
        raise RunnerError(str(error)) from error
    if json.dumps(policy, sort_keys=True) != json.dumps(_suite_policy_metadata(weights, plan), sort_keys=True):
        raise RunnerError("shard policy differs from the current deterministic four-partition plan")
    source = bindings[0][0]
    root = Path(__file__).resolve().parents[1]
    try:
        current = snapshot(root, exclude=tuple(paths))
    except LocalCIError as error:
        raise RunnerError(f"cannot verify reconciliation source: {error}") from error
    if (source["checkout_sha"] != current["checkout_sha"]
            or source["checkout_tree"] != current["checkout_tree"]
            or source["before_digest"] != current["digest"]):
        raise RunnerError("shard source differs from the current checkout inputs")
    seen: set[str] = set()
    record_map: dict[str, dict[str, object]] = {}
    summaries = []
    for index, identity in enumerate(identities):
        report = by_shard[identity]
        ids = _required_ids(report, "selection_manifest")
        if ids != plan.shard_ids[index] or tuple(item for item in canonical_ids if item in set(ids)) != ids:
            raise RunnerError(f"shard {identity} differs from its ordered partition plan")
        if seen.intersection(ids):
            raise RunnerError("shard selections overlap")
        seen.update(ids)
        record_map.update((row["canonical_id"], row) for row in report["tests"])
        summaries.append({"identity": identity, "outcome": report["outcome"],
                          "selection_manifest": report["selection_manifest"], "totals": report["totals"],
                          "durations": durations_by_shard[identity], "details": report["details"]})
    if seen != set(canonical_ids):
        raise RunnerError("shard selections are not exhaustive")
    walls = [durations_by_shard[key]["wall_seconds"] for key in identities]
    return {
        "schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME, "kind": "reconciliation",
        "outcome": "success" if all(item["outcome"] == "success" for item in summaries) else "failure",
        "canonical_manifest": canonical, "suite_policy": policy, "source_binding": source,
        "common_environment": bindings[0][1], "shards": summaries,
        "tests": [record_map[identifier] for identifier in canonical_ids],
        "totals": {"selected": len(canonical_ids), "completed": sum(by_shard[key]["totals"]["completed"] for key in identities)},
        "durations": {"sequential_equivalent_wall_seconds": sum(walls), "concurrent_wall_seconds": max(walls),
                      "cumulative_test_seconds": sum(durations_by_shard[key]["cumulative_test_seconds"] for key in identities)},
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
        command.add_argument("--shard", choices=("1/4", "2/4", "3/4", "4/4"),
                             help="one complete partition of the four-shard suite")
        command.add_argument("--json")
        if name == "run":
            command.add_argument("--slowest", type=int, default=20)
            command.add_argument("--failfast", action="store_true")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("reports", nargs=4, metavar="REPORT",
                           help="one current report for each of 1/4 through 4/4")
    reconcile.add_argument("--json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "reconcile":
            report = reconcile_reports(args.reports)
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
        # Hosted shards retain their existing execution behavior. Independent
        # local invocations share host admission across every checkout.
        from tests.local_ci import runner_admission
        from tests.local_ci_state import LocalCIError

        try:
            admitted = runner_admission(list(argv) if argv is not None else sys.argv[1:])
        except LocalCIError as error:
            raise RunnerError(str(error)) from error
        if admitted is not None:
            return admitted
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
        return 0 if report["outcome"] == "success" else 1
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
