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
            "isolated_test_id": suite_policy.ISOLATED_TEST_ID,
            "isolated_weight_basis": suite_policy.ISOLATED_WEIGHT_BASIS,
            "digest": plan.digest, "group_count": len(plan.group_weights_microseconds),
            "fallback_group_count": len(plan.fallback_groups),
            "shards": [
                {"identity": f"{index + 1}/{suite_policy.SHARD_COUNT}", "group_count": len(plan.shard_groups[index]),
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
        if shard not in {f"{index}/{suite_policy.SHARD_COUNT}" for index in range(1, suite_policy.SHARD_COUNT + 1)}:
            raise RunnerError("--shard must be one of 1/5 through 5/5")
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
        stream=stream, verbosity=2, failfast=failfast, resultclass=result_factory,
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
    token = os.environ.get(_METHOD_TOKEN_ENV)
    if token is not None:
        if len(token) != 32 or set(token) - set("0123456789abcdef"):
            raise RunnerError("invalid method child token")
        report["method_child"] = {"version": 1, "token": token, "pid": os.getpid(), "parent_pid": os.getppid()}
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
    """Validate the complete current five-report contract and retain its records."""
    identities = tuple(f"{index}/{suite_policy.SHARD_COUNT}" for index in range(1, suite_policy.SHARD_COUNT + 1))
    if len(paths) != suite_policy.SHARD_COUNT or len(set(paths)) != suite_policy.SHARD_COUNT:
        raise RunnerError("reconcile requires five distinct shard report paths")
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
            raise RunnerError("reports must have complementary unique five-shard identities")
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
    modes = {_execution_metadata(report) for report in by_shard.values()}
    if len(modes) != 1:
        raise RunnerError("mixed execution modes, profiles or timing bases")
    mode, profile, timing_basis = modes.pop()
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
        raise RunnerError("shard policy differs from the current deterministic five-partition plan")
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
        "common_environment": bindings[0][1],
        "execution": {"version": 1, "mode": mode, "profile": profile, "timing_basis": timing_basis}, "shards": summaries,
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


# This closed profile is audited for fresh whole-method processes. Adding a method
# (even in one of these classes) requires another fixture/eligibility review.
_HOSTED_PROFILE = 'audited-147-v3'
_HOSTED_CLEANUP_SECONDS = 30.0
_HOSTED_LAUNCH_IDS = (
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_front_schema_is_minimal_closed_and_provider_strict',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_intent_variants_and_objective_values_are_closed_and_content_safe',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_readable_and_session_context_include_objectives_without_new_records',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_ordinary_discussion_captures_current_state_without_specialists',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_provider_authority_fields_fall_back_without_candidate',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_failure_correlation_and_post_closure_calls_fail_closed',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_whole_and_range_conflict_bind_proposal_and_detect_drift',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_safe_replay_preserves_wire_and_counts_distinct_physical_calls',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_unstarted_prepared_and_submitted_requests_never_replay',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_old_drafted_revision_cannot_authorize_revised_candidate',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_empty_inventory_does_not_add_unaffected_owners_and_mixed_scope_does',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_direct_scope_is_required_and_substantive_outcomes_cannot_bypass_routing',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_rejected_returned_result_closes_and_next_turn_uses_one_fresh_call',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_mismatch_reject_revise_and_free_text_are_non_authorizing',
    'tests.test_lead_session_continuity.LeadSessionContinuityTests.test_confirmed_plan_survives_three_followups_and_conversation_switch',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_core_conflict_revision_and_manuscript_atomic_rename_workflows',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_draft_integrates_exact_all_role_review_apply_and_storage_parity',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_revision_direct_workflow_cannot_erase_selected_story_scope',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_all_assessments_are_preserved_and_partial_failure_never_reviews',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_direct_search_read_answer_uses_bounded_history_without_proposal',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_zero_hits_and_scoped_status_evidence_are_derived_locally',
    'tests.test_lead_artifact_objectives.LeadArtifactObjectiveTests.test_only_matching_successful_apply_satisfies_objective',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_unread_and_truncated_search_fall_back_while_reads_attach_locally',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_unknown_duplicate_reordered_and_prior_turn_ids_fail_once',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_current_response_alias_binds_exact_whole_message_before_action',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_budget_notice_preserves_maximum_utf8_synthesis',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_empty_continuation_retains_selection_and_cancel_is_terminal',
    'tests.test_lead_orchestration.LeadReferenceFailurePrecedenceTests.test_primary_reference_and_capacity_errors_are_not_reclassified',
    'tests.test_lead_recommendations.LeadRecommendationTests.test_exact_stale_duplicate_and_concurrent_decisions_never_reuse_authority',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_runtime_repairs_then_safely_ignores_invalid_proposals',
    'tests.test_lead_recommendations.LeadRecommendationTests.test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_forced_timeout_restart_and_explicit_retry_preserve_both_attempts',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_projection_retains_satisfaction_across_later_apply_commit_cycles',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_search_read_answer_can_add_an_artifact_candidate',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_strict_provider_outcomes_reject_unknown_private_mixed_and_duplicate',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_later_action_recovery_preserves_prior_evidence_and_per_call_cap',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_all_five_outcomes_have_exact_visible_next_actions',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_unaffordable_continuation_finalizes_exact_result_without_authority',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_front_schema_rejection_is_repaired_once',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_literal_lead_turn_and_consultation_decisions_are_exact',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_cross_conversation_selection_preserves_scope_and_rejects_workflow_relabel',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_stale_confirmation_and_revision_require_fresh_authority',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_only_budget_subtype_is_caught_after_successful_result',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_declared_substantive_direct_outcomes_fail_closed',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_search_before_first_rejection_uses_attempt_labels',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_question_and_synthesis_sources_preserve_exact_whole_message_digests',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_confirm_origin_v2_and_continuing_v1_preserve_exact_wire_facts',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_legacy_bare_prepared_and_submitted_tails_close_only_on_new_input',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_abandoned_turn_preserves_other_conversation_successful_checkpoint',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_invalid_specialist_shapes_repair_then_fall_back_without_halting',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_review_rejects_empty_reference_but_accepts_explicit_user_candidate',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_restart_and_repeated_known_result_finalization_are_idempotent',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_validation_failure_closes_once_and_restart_uses_fresh_turn',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_read_evidence_translates_whole_content_and_range_boundary_digests',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_objective_history_round_trips_through_filesystem_sqlite_and_archive',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_empty_core_status_evidence_requires_selected_fresh_zero_byte_source',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_history_rejects_foreign_or_changed_origin_predecessor_sources_and_targets',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_matching_unfinished_legacy_operation_retires_native_context',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_v1_objective_wire_round_trips_without_changing_legacy_bytes',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_failure_closure_write_error_keeps_history_gated',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_attached_reference_cancellation_closes_and_releases_for_explicit_retry',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_domain_rejection_details_survive_restarted_finalization',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_revised_artifact_intent_replaces_candidate_in_the_same_step',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_recovered_reply_retries_transient_validation_publication',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_one_and_two_assignments_run_with_exact_parallel_width',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_restart_after_completed_execution_accepts_a_new_lead_turn',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_revision_timeout_keeps_superseded_confirmation_non_authorizing',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_draft_task_cannot_skip_current_revision_drafting_via_review_artifact_intent',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_empty_whole_is_evidence_but_missing_and_invalid_range_fail',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_empty_target_adequate_brief_produces_draft_without_writes',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_reviewed_history_transfers_with_filesystem_sqlite_parity',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_mixed_zero_hit_survives_self_appended_history_without_mutation',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_incomplete_core_workflow_is_reviewed_only_after_confirmation',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_zero_hit_source_drift_blocks_confirmation_before_specialists',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_durable_zero_hit_history_contains_no_search_or_source_content',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_pre_submission_correction_failure_closes_first_rejected_call',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_archived_task_is_context_without_routable_authority',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_response_correction_requires_capacity',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_untrusted_answer_diagnostic_cannot_publish_private_text',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_readable_and_session_context_expose_exact_origin_without_private_authority',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_corrupt_failed_history_cannot_admit_provider_work',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_recommendation_controls_route_exact_decisions_and_cancel_without_mutation',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_recommendation_revision_cancel_and_eof_preserve_pending_request',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_execution_delivers_only_each_assignments_ordered_reference_subset',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_second_round_uses_reply_events_fresh_authority_and_cannot_chain_again',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_concurrent_duplicate_domain_and_schema_reports_keep_call_order',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_recommendation_and_objective_v2_round_trip_through_both_stores_and_archive',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_history_rejects_foreign_origin_tail_and_nonatomic_action_batches',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_specialist_and_synthesis_validation_details_are_private_and_correlated',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_synthesis_contract_is_semantic_and_runtime_derives_attribution',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_restarted_finalization_restores_only_this_execution_diagnostics',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_action_values_and_decisions_are_closed_exact_and_immutable',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_outer_cancellation_joins_tasks_persists_terminals_and_cleans_marker',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_synthesis_derives_non_supporting_inputs_locally',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_synthesis_cancellation_preserves_specialist_result_and_never_reruns_it',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_submitted_uncertainty_enters_unknown_and_never_replays',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_persistent_validation_publication_failure_keeps_result_unknown',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_failed_specialist_retries_transient_validation_publication',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_confirm_starts_one_atomic_dispatch_batch_then_runs_three_in_parallel',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_original_and_repair_validation_details_persist_across_restart',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_assignment_failure_joins_siblings_and_skips_synthesis',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_start_append_failure_makes_zero_specialist_calls_and_leaks_no_marker',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_confirmed_restart_preserves_evidence_and_requires_fresh_authority',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_non_string_lead_kind_reports_exact_type_and_common_issues',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_kind_bearing_lead_result_reports_common_anyof_issues',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_confirm_is_exact_single_use_and_executes_without_artifact_effect',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_huge_invalid_array_truncates_safe_durable_report',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_republishing_domain_notice_is_idempotent',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_long_validation_details_survive_history_restart_in_chunks',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_validation_details_do_not_grant_action_authority',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_second_returned_rejection_closes_against_last_call',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_runtime_constructs_complete_ordered_content_free_proposal',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_scoped_request_digest_and_legacy_absent_scope_remain_distinct',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_confirmed_live_capability_preserves_exact_evidence_without_dispatch',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_restored_results_keep_strict_evidence_and_provider_free_compatibility',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_legacy_pending_confirmation_requires_fresh_scoped_proposal_without_rewrite',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_legacy_judgments_synthesis_and_runtime_notices_never_gain_action_authority',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_wrong_task_pair_rejects_before_dispatch',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_no_repair_capacity_preserves_validation_notice_without_authority',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_domain_rejection_without_repair_persists_safe_notice',
    'tests.test_lead_orchestration.LeadReferenceFailurePrecedenceTests.test_reference_failure_with_unwritable_history_stays_recovery_required',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_reference_drift_blocks_confirmation_without_calls',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_minimal_reference_answer_needs_no_provider_correction',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_retry_lead_turn_keeps_fixed_diagnostic_copy_and_renders_error_role',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_provider_free_advisory_confirms_one_specialist_without_synthesis_or_chain',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_unresolved_running_becomes_outcome_unknown_without_provider',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_initial_budget_rejection_remains_failure',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_capability_is_private_nonserializable_and_single_use',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_backend_route_session_thread_and_workspace_drift_veto_replay',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_provider_free_advisory_requires_scope_and_separate_confirmation',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_local_reference_load_does_not_consume_provider_capacity',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_replay_requires_explicit_exact_true_validator',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_admission_race_abort_keeps_original_accounting_and_exchanges',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_unsafe_unconfirmed_and_repeated_auth_failure_never_replay',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_source_drift_vetoes_replay_before_submission',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_auth_cancel_cleans_validator_and_retains_prior_call_facts',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_noninteractive_auth_errors_and_restart_never_replay',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_provider_invocation_failure_writes_error_notice_without_crashing',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_artifact_and_commit_controls_are_distinct_and_correlated',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_pending_commit_redisclosure_reuses_exact_picker_without_apply',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_unexpected_exception_in_submit_propagates_to_fatal_boundary',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_run_operation_returns_task_result_when_task_completes_after_auth_ready',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_failure_interruption_and_recovery_turns_never_replay_authority',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_ordinary_lead_progress_sink_finishes_before_lead_and_elapsed_output',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_confirmed_execution_writes_specialists_then_lead_then_elapsed',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_provider_invocation_failure_surfaces_transient_stream_error_in_final_output',
)
_HOSTED_METHOD_IDS = tuple(sorted(_HOSTED_LAUNCH_IDS))
_HOSTED_PRIORITY_ID = _HOSTED_LAUNCH_IDS[0]
_METHOD_TOKEN_ENV = "STORY_TEST_METHOD_CHILD_TOKEN"


def _hosted_capacity() -> int:
    capacity = os.cpu_count()
    if type(capacity) is not int or capacity < 1:
        raise RunnerError("hosted method execution requires known CPU capacity")
    if hasattr(os, "sched_getaffinity"):
        capacity = min(capacity, len(os.sched_getaffinity(0)))
    if capacity < 1:
        raise RunnerError("hosted method execution has no available CPU")
    return min(4, capacity)


def _hosted_options(args) -> dict[str, object] | None:
    profile, deadline = args.hosted_methods, args.hosted_deadline_epoch
    if profile is None and deadline is None:
        return None
    if (profile != _HOSTED_PROFILE or args.command != "run" or not args.shard
            or args.id or args.module or args.tier or args.failfast
            or type(args.json) is not str or not args.json):
        raise RunnerError("hosted methods require the audited profile and one full shard")
    forbidden = ("STORY_WIRE_TEST_CAPTURE_DIR", _METHOD_TOKEN_ENV, "STORY_LOCAL_CI_TOKEN",
                 "STORY_LOCAL_CI_WORKER_STATE", "STORY_LOCAL_CI_GUEST_STATE")
    if (os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux"
            or any(name in os.environ for name in forbidden)):
        raise RunnerError("hosted methods require an isolated hosted Linux environment")
    try:
        limits = [float(os.environ[name]) - 450 for name in ("TEST_RUN_DEADLINE", "TEST_JOB_DEADLINE")]
        deadline = float(deadline)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise RunnerError("hosted methods require the original absolute worker deadline") from error
    now = time.time()
    if (not all(math.isfinite(value) for value in [deadline, *limits])
            or not now < deadline <= min(*limits, now + 3600)):
        raise RunnerError("hosted method deadline exceeds the grant or cleanup reserve")
    started = time.monotonic()
    work_deadline = started + (deadline - now)
    return {"started": work_deadline - 3600, "work_deadline": work_deadline,
            "deadline": work_deadline + _HOSTED_CLEANUP_SECONDS,
            "work_deadline_epoch": deadline, "cleanup_deadline_epoch": deadline + _HOSTED_CLEANUP_SECONDS,
            "report_path": args.json, "capacity": _hosted_capacity()}


def _hosted_partition(canonical: CanonicalCollection, selection: CanonicalCollection):
    eligible = set(_HOSTED_METHOD_IDS)
    classes = {identifier.rsplit(".", 1)[0] for identifier in eligible}
    present = {identifier for identifier in canonical.ids if identifier.rsplit(".", 1)[0] in classes}
    if present != eligible or len(eligible) != 147:
        raise RunnerError("audited whole-method eligibility changed")
    serial = tuple(test for test in selection.tests if test.id() not in eligible)
    scheduled = tuple(identifier for identifier in selection.ids if identifier in eligible)
    return CanonicalCollection(serial, tuple(test.id() for test in serial)), scheduled


def _hosted_launch_order(scheduled):
    rank = {identifier: index for index, identifier in enumerate(_HOSTED_LAUNCH_IDS)}
    return tuple(sorted(scheduled, key=rank.__getitem__))


def _hosted_artifacts(report_path: str) -> Path:
    root = Path(report_path + ".methods")
    root.mkdir(mode=0o700)
    return root


def _method_group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _method_supervisor(config_path: str) -> int:
    """Private Linux entry: owns one worker group, never discovers applications."""
    import ctypes
    import select
    import signal
    import subprocess
    config = _load_json_object(config_path)
    required = {"version", "argv", "cwd", "read_fd", "work_deadline", "deadline", "token", "receipt", "log"}
    if (set(config) != required or type(config["version"]) is not int
            or config["version"] != 1 or sys.platform != "linux"):
        raise RunnerError("invalid method supervisor configuration")
    command, work_deadline, deadline = config["argv"], config["work_deadline"], config["deadline"]
    if (type(command) is not list or not command or command[0] != sys.executable
            or any(type(value) is not str or not value for value in command)
            or type(config["read_fd"]) is not int or config["read_fd"] < 0
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (work_deadline, deadline))
            or deadline != work_deadline + _HOSTED_CLEANUP_SECONDS
            or type(config["token"]) is not str or len(config["token"]) != 32
            or set(config["token"]) - set("0123456789abcdef")):
        raise RunnerError("invalid method supervisor grant")
    # Linux PR_SET_CHILD_SUBREAPER: adopted descendants can be reaped after the
    # direct Popen child has been waited. Never consume Popen's status via waitpid.
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0) != 0:
        raise RunnerError("method supervisor requires Linux child-subreaper ownership")
    receipt = {"version": 1, "token": config["token"], "supervisor_pid": os.getpid(),
               "worker_pid": None, "worker_exit": None, "reason": "not_started", "reaped": False,
               "group_absent": False, "complete": False, "work_deadline": work_deadline, "deadline": deadline}
    process = None
    interrupted = False
    old_handlers = {}
    def request_stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    for signum in (signal.SIGINT, signal.SIGTERM):
        old_handlers[signum] = signal.signal(signum, request_stop)
    cleanup_stop = deadline - 5  # Retain five seconds for receipt publication and parent reaping.
    read_fd = config["read_fd"]
    os.set_blocking(read_fd, False)
    try:
        if interrupted or time.monotonic() >= work_deadline or select.select([read_fd], [], [], 0)[0]:
            receipt["reason"] = "no_grant_or_parent"
            return 1
        environment = dict(os.environ, **{_METHOD_TOKEN_ENV: config["token"]})
        with open(config["log"], "xb", buffering=0) as log:
            if interrupted or time.monotonic() >= work_deadline:
                receipt["reason"] = "no_grant_or_parent"
                return 1
            process = subprocess.Popen(command, cwd=config["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            receipt["worker_pid"] = process.pid
            receipt["reason"] = "completed"
            while process.poll() is None:
                remaining = work_deadline - time.monotonic()
                if interrupted or remaining <= 0:
                    receipt["reason"] = "interrupted" if interrupted else "deadline"
                    break
                if select.select([read_fd], [], [], min(0.1, remaining))[0]:
                    receipt["reason"] = "parent_closed"
                    break
    finally:
        if process is not None:
            if not _method_group_absent(process.pid):
                if process.poll() is not None and receipt["reason"] == "completed":
                    receipt["reason"] = "descendants_survived"
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                interrupt_end = min(cleanup_stop, time.monotonic() + 5)
                while time.monotonic() < interrupt_end:
                    if process.poll() is not None:
                        # Only now may waitpid reap adopted descendants.
                        try:
                            while time.monotonic() < cleanup_stop and os.waitpid(-1, os.WNOHANG)[0]:
                                pass
                        except ChildProcessError:
                            pass
                    if _method_group_absent(process.pid):
                        break
                    time.sleep(min(0.05, max(0, interrupt_end - time.monotonic())))
                if not _method_group_absent(process.pid):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            while time.monotonic() < cleanup_stop:
                if process.poll() is not None:
                    receipt["worker_exit"] = process.returncode
                    receipt["reaped"] = True
                    try:
                        while time.monotonic() < cleanup_stop and os.waitpid(-1, os.WNOHANG)[0]:
                            pass
                    except ChildProcessError:
                        pass
                if receipt["reaped"] and _method_group_absent(process.pid):
                    receipt["group_absent"] = True
                    break
                time.sleep(min(0.05, max(0, cleanup_stop - time.monotonic())))
        receipt["complete"] = bool(receipt["reaped"] and receipt["group_absent"])
        receipt["finished"] = time.monotonic()
        _write_json(config["receipt"], receipt)
        os.close(read_fd)
        for signum, previous in old_handlers.items():
            signal.signal(signum, previous)
    return 0 if receipt["complete"] and receipt["reason"] == "completed" else 1


def _launch_method(command, directory: Path, *, work_deadline: float):
    import secrets
    import subprocess
    if (type(work_deadline) not in (int, float) or not math.isfinite(work_deadline)
            or time.monotonic() >= work_deadline):
        raise RunnerError("method work deadline exhausted before launch")
    deadline = work_deadline + _HOSTED_CLEANUP_SECONDS
    token = secrets.token_hex(16)
    read_fd, write_fd = os.pipe()
    config = {"version": 1, "argv": list(command), "cwd": str(Path(__file__).resolve().parents[1]),
              "read_fd": read_fd, "deadline": deadline, "work_deadline": work_deadline,
              "token": token, "receipt": str(directory / "supervisor.json"), "log": str(directory / "worker.log")}
    _write_json(str(directory / "config.json"), config)
    try:
        if time.monotonic() >= work_deadline:
            raise RunnerError("method work deadline exhausted before supervisor launch")
        process = subprocess.Popen([sys.executable, "-m", "tests.runner", "--_method-supervisor",
                                    str(directory / "config.json")], cwd=config["cwd"], pass_fds=(read_fd,),
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return process, write_fd, config


def _method_receipt(config, process):
    receipt = _load_json_object(config["receipt"])
    keys = {"version", "token", "supervisor_pid", "worker_pid", "worker_exit", "reason", "reaped",
            "group_absent", "complete", "work_deadline", "deadline", "finished"}
    if (set(receipt) != keys or type(receipt["version"]) is not int or receipt["version"] != 1
            or receipt["token"] != config["token"] or type(receipt["supervisor_pid"]) is not int
            or receipt["supervisor_pid"] != process.pid or type(receipt["worker_pid"]) is not int
            or receipt["reason"] not in ("completed", "descendants_survived", "interrupted", "deadline", "parent_closed")
            or receipt["worker_pid"] <= 1 or type(receipt["worker_exit"]) is not int
            or any(receipt[name] is not True for name in ("reaped", "group_absent", "complete"))
            or receipt["work_deadline"] != config["work_deadline"] or receipt["deadline"] != config["deadline"]
            or type(receipt["finished"]) not in (int, float) or not math.isfinite(receipt["finished"])
            or receipt["finished"] > config["deadline"] or not _method_group_absent(receipt["worker_pid"])):
        raise RunnerError("method supervisor cleanup proof is incomplete")
    return receipt


def _validate_method_report(report, ids, canonical_ids, binding, environment, caps, exit_code):
    required = {"schema_version", "runner", "kind", "outcome", "canonical_manifest", "selection_manifest",
                "tests", "totals", "unexecuted_ids", "durations", "slowest", "details", "source_binding",
                "environment", "environment_valid"}
    if type(report) is not dict or not required <= set(report) <= required | {"selection", "suite_policy"}:
        raise RunnerError("method report fields differ from the serial contract")
    if (report.get("schema_version") != SCHEMA_VERSION or report.get("runner") != RUNNER_NAME
            or report.get("kind") != "run_result" or report.get("outcome") not in ("success", "failure")
            or type(exit_code) is not int or exit_code != (0 if report["outcome"] == "success" else 1)
            or _required_ids(report, "canonical_manifest") != tuple(canonical_ids)
            or _required_ids(report, "selection_manifest") != tuple(ids) or report.get("unexecuted_ids") != []
            or _required_report_identity(report)[0] != binding
            or report.get("environment") != environment or "execution" in report):
        raise RunnerError("method report identity, source or completion differs")
    rows, totals, details = report.get("tests"), report.get("totals"), report.get("details")
    total_keys = {"selected", "started", "completed", "passed", "failures", "errors", "skipped",
                  "expected_failures", "unexpected_successes", "interrupted"}
    if (type(rows) is not list or type(totals) is not dict or set(totals) != total_keys
            or any(type(value) is not int or value < 0 for value in totals.values())
            or any(totals[key] != len(ids) for key in ("selected", "started", "completed"))
            or totals["interrupted"] or type(details) is not list
            or any(type(row) is not dict for row in rows)
            or tuple(row.get("canonical_id") for row in rows) != tuple(ids)):
        raise RunnerError("method report has incomplete or duplicate records")
    terminal = {"passed", "failed", "error", "skipped", "expected_failure", "unexpected_success"}
    for row in rows:
        duration, count = row.get("duration_seconds"), row.get("subtest_count")
        if (set(row) != {"canonical_id", "status", "duration_seconds", "subtest_count"}
                or row["status"] not in terminal or type(count) is not int or not 0 <= count <= caps[row["canonical_id"]]
                or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
            raise RunnerError("method report has invalid terminal data or subtest count")
    kinds = {"failures": {"failure", "subtest_failure"}, "errors": {"error", "subtest_error"},
             "skipped": {"skip"}, "expected_failures": {"expected_failure"},
             "unexpected_successes": {"unexpected_success"}}
    allowed = set().union(*kinds.values())
    if any(type(item) is not dict or item.get("canonical_id") not in ids or item.get("kind") not in allowed
           or (item["kind"] == "skip" and type(item.get("reason")) is not str) for item in details):
        raise RunnerError("method report details differ from selected IDs")
    for item in details:
        extra = {"reason"} if item["kind"] == "skip" else (set() if item["kind"] == "unexpected_success" else {"traceback"})
        if (set(item) != {"canonical_id", "display_id", "kind"} | extra
                or any(type(value) is not str for value in item.values()) or not item["display_id"]):
            raise RunnerError("method report observation is malformed")
    if report["slowest"] != []:
        raise RunnerError("method component report must retain the zero-slowest request")
    if any(totals[key] != sum(item["kind"] in values for item in details) for key, values in kinds.items()):
        raise RunnerError("method report outcome counts disagree")
    priority = {"skip": "skipped", "failure": "failed", "subtest_failure": "failed", "error": "error",
                "subtest_error": "error", "expected_failure": "expected_failure", "unexpected_success": "unexpected_success"}
    for row in rows:
        statuses = [priority[item["kind"]] for item in details if item["canonical_id"] == row["canonical_id"]]
        if row["status"] != max(["passed", *statuses], key=TimedTextTestResult._PRIORITY.__getitem__):
            raise RunnerError("method report status disagrees with details")
    if (totals["passed"] != sum(row["status"] == "passed" for row in rows)
            or (report["outcome"] == "success") != (not any(totals[key] for key in ("failures", "errors", "unexpected_successes")))):
        raise RunnerError("method report outcome disagrees with records")
    durations = _required_durations(report)
    if not math.isclose(durations["cumulative_test_seconds"], sum(row["duration_seconds"] for row in rows),
                        rel_tol=1e-12, abs_tol=1e-9):
        raise RunnerError("method report cumulative duration differs")
    return rows, details, totals


def _merge_method_reports(canonical, selection, reports, *, binding, environment, caps, wall, metadata, errors, slowest):
    rows, details, totals = {}, [], {key: 0 for key in ("selected", "started", "completed", "passed", "failures",
                                                     "errors", "skipped", "expected_failures", "unexpected_successes", "interrupted")}
    for report, ids, exit_code in reports:
        records, observations, counts = _validate_method_report(report, ids, canonical.ids, binding, environment, caps, exit_code)
        if any(row["canonical_id"] in rows or row["canonical_id"] not in selection.ids for row in records):
            raise RunnerError("method report overlaps another report or selection")
        rows.update((row["canonical_id"], row) for row in records)
        details.extend(observations)
        for key in totals:
            totals[key] += counts[key]
    details.sort(key=lambda item: selection.ids.index(item["canonical_id"]))
    records = [rows[identifier] for identifier in selection.ids if identifier in rows]
    totals["selected"] = len(selection.ids)
    unexecuted = [identifier for identifier in selection.ids if identifier not in rows]
    failure = bool(errors or unexecuted or any(totals[key] for key in ("failures", "errors", "unexpected_successes")))
    report = {"schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME, "kind": "run_result",
              "outcome": "failure" if failure else "success", "canonical_manifest": _manifest(canonical.ids),
              "selection_manifest": _manifest(selection.ids), "tests": records, "totals": totals,
              "unexecuted_ids": unexecuted, "durations": {"wall_seconds": wall,
              "cumulative_test_seconds": sum(row["duration_seconds"] for row in records)},
              "slowest": sorted(records, key=lambda row: (-row["duration_seconds"], row["canonical_id"]))[:slowest],
              "details": details, "source_binding": binding, "environment": environment, "environment_valid": True}
    report.update(metadata)
    return report


def _hosted_execute(canonical, selected, options, *, slowest, stream):
    import signal
    import subprocess
    serial, scheduled = _hosted_partition(canonical, selected.selection)
    caps = _load_json_object(str(CANONICAL_INVENTORY_PATH))["subtest_caps"]
    if tuple(sorted(caps)) != canonical.ids or any(type(cap) is not int or cap < 0 for cap in caps.values()):
        raise RunnerError("hosted method inventory differs from frozen caps")
    root = _hosted_artifacts(options["report_path"])
    before, environment = _source_snapshot(), _execution_environment()
    binding = {"checkout_sha": before["checkout_sha"], "checkout_tree": before["checkout_tree"],
               "before_digest": before["digest"], "after_digest": before["digest"], "source_valid": True}
    reports, errors, active = [], [], {}
    deadline, capacity = options["deadline"], options["capacity"]
    peak = launched = completed = 0
    cleanup_complete = True
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    previous = {value: signal.getsignal(value) for value in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM)}
    if previous_timer != (0.0, 0.0):
        raise RunnerError("hosted method execution cannot replace an existing alarm")
    def stop(signum, frame):
        raise KeyboardInterrupt
    for value in previous:
        signal.signal(value, stop)
    work_deadline = options["work_deadline"]
    try:
        if time.monotonic() >= work_deadline:
            raise RunnerError("hosted method work grant exhausted before serial traversal")
        signal.setitimer(signal.ITIMER_REAL, work_deadline - time.monotonic())
        if serial.ids:
            execution = _execute(serial, stream=stream)
            report = _run_report(canonical, serial, execution, slowest=0)
            if execution.interrupted or report["unexecuted_ids"] or report["totals"]["completed"] != len(serial.ids):
                errors.append("serial traversal interrupted or incomplete")
                # The original report already records only observed callbacks.
                reports.append((report, serial.ids, 130))
            else:
                reports.append((report, serial.ids, 0 if report["outcome"] == "success" else 1))
        pending = iter(_hosted_launch_order(scheduled))
        exhausted = bool(errors)
        try:
            while active or not exhausted:
                while not exhausted and len(active) < capacity:
                    if time.monotonic() >= work_deadline:
                        raise KeyboardInterrupt
                    identifier = next(pending, None)
                    if identifier is None:
                        exhausted = True
                        break
                    directory = root / str(launched)
                    directory.mkdir(mode=0o700)
                    command = [sys.executable, "-m", "tests.runner", "run", "--id", identifier,
                               "--json", str(directory / "report.json"), "--slowest", "0"]
                    process, pipe, config = _launch_method(command, directory, work_deadline=work_deadline)
                    active[process.pid] = (process, pipe, config, identifier, directory)
                    launched += 1
                    peak = max(peak, len(active))
                for pid, (process, pipe, config, identifier, directory) in tuple(active.items()):
                    if process.poll() is None:
                        continue
                    os.close(pipe)
                    del active[pid]
                    custody_verified = False
                    try:
                        receipt = _method_receipt(config, process)
                        custody_verified = True
                        if process.returncode != 0 or receipt["reason"] != "completed":
                            raise RunnerError("method supervisor did not complete normally")
                        child = _load_json_object(str(directory / "report.json"))
                        identity = {"version": 1, "token": config["token"], "pid": receipt["worker_pid"],
                                    "parent_pid": process.pid}
                        captured = child.pop("method_child", None)
                        if (type(captured) is not dict or captured != identity
                                or any(type(captured[name]) is not int for name in ("version", "pid", "parent_pid"))):
                            raise RunnerError("method report is stale or from another child")
                        if child.get("selection") != {"tiers": [], "shard": None}:
                            raise RunnerError("method child must use the original serial ID selector")
                        _validate_method_report(child, (identifier,), canonical.ids, binding, environment, caps,
                                                receipt["worker_exit"])
                        reports.append((child, (identifier,), receipt["worker_exit"]))
                        completed += 1
                    except (RunnerError, OSError) as error:
                        errors.append(f"{identifier}: {error}")
                        cleanup_complete = cleanup_complete and custody_verified
                        exhausted = True
                if active:
                    time.sleep(min(0.05, max(0, work_deadline - time.monotonic())))
        except KeyboardInterrupt:
            errors.append("hosted method work interrupted or deadline exhausted")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(signum, signal.SIG_IGN)
            # Closing the liveness pipe makes each independent supervisor
            # clean its worker group, even when this parent cannot signal it.
            for process, pipe, config, identifier, directory in active.values():
                os.close(pipe)
            for process, pipe, config, identifier, directory in active.values():
                remaining = max(0, deadline - 2 - time.monotonic())
                try:
                    process.wait(timeout=remaining)
                    _method_receipt(config, process)
                except (OSError, RunnerError, subprocess.TimeoutExpired):
                    cleanup_complete = False
                    errors.append(f"{identifier}: supervisor cleanup incomplete")
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=max(0, deadline - time.monotonic()))
                        except (ProcessLookupError, subprocess.TimeoutExpired):
                            pass
            active.clear()
    except KeyboardInterrupt:
        errors.append("hosted serial work interrupted or deadline exhausted")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for value, handler in previous.items():
            signal.signal(value, handler)
    after = _source_snapshot()
    environment_valid = environment == _execution_environment()
    if before != after or not environment_valid:
        errors.append("hosted method source or environment changed")
    execution_metadata = {"version": 1, "mode": "hosted-whole-method", "profile": _HOSTED_PROFILE,
                          "profile_manifest": _manifest(_HOSTED_METHOD_IDS), "serial_manifest": _manifest(serial.ids),
                          "scheduled_manifest": _manifest(scheduled),
                 "launch_manifest": _manifest(_hosted_launch_order(scheduled)),
                 "work_deadline_epoch": options["work_deadline_epoch"],
                 "cleanup_deadline_epoch": options["cleanup_deadline_epoch"],
                 "capacity": capacity, "peak_children": peak,
                          "launched": launched, "completed": completed, "cleanup_complete": cleanup_complete,
                          "timing_basis": "observed-parent-wall-and-test-sum", "infrastructure_errors": errors}
    # Invalid/partial child reports never become synthetic test rows. For an
    # interrupted serial suite retain its already-observed rows separately.
    interrupted = [item for item in reports if item[2] == 130]
    complete = [item for item in reports if item[2] != 130]
    report = _merge_method_reports(canonical, selected.selection, complete, binding=binding, environment=environment,
                                  caps=caps, wall=max(0, time.monotonic() - options["started"]),
                                  metadata=selected.metadata, errors=errors, slowest=slowest)
    if interrupted:
        partial = interrupted[0][0]
        report["tests"] = partial["tests"]
        report["details"] = partial["details"]
        report["totals"] = {**partial["totals"], "selected": len(selected.selection.ids)}
        observed = {row["canonical_id"] for row in partial["tests"]}
        report["unexecuted_ids"] = [identifier for identifier in selected.selection.ids if identifier not in observed]
        report["durations"]["cumulative_test_seconds"] = partial["durations"]["cumulative_test_seconds"]
        report["slowest"] = partial["slowest"]
    report["source_binding"] = {**binding, "after_digest": after["digest"], "source_valid": before == after}
    report["execution"] = execution_metadata
    report["environment_valid"] = environment_valid
    report["durations"]["wall_seconds"] = max(0, time.monotonic() - options["started"])
    return report


def _method_manifest_equal(value, ids):
    return (type(value) is dict and type(value.get("count")) is int
            and type(value.get("ordered_ids")) is list and type(value.get("digest")) is str
            and all(type(item) is str for item in value["ordered_ids"]) and value == _manifest(ids))


def _execution_metadata(report):
    value = report.get("execution")
    if "execution" not in report:
        return ("serial", None, "sum-observed-shard-walls")
    keys = {"version", "mode", "profile", "profile_manifest", "serial_manifest", "scheduled_manifest", "capacity",
            "peak_children", "launched", "completed", "cleanup_complete", "timing_basis", "infrastructure_errors",
            "launch_manifest", "work_deadline_epoch", "cleanup_deadline_epoch"}
    ids = tuple(_required_dict(report, "selection_manifest")["ordered_ids"])
    scheduled = tuple(identifier for identifier in ids if identifier in _HOSTED_METHOD_IDS)
    serial = tuple(identifier for identifier in ids if identifier not in _HOSTED_METHOD_IDS)
    if (type(value) is not dict or set(value) != keys or type(value["version"]) is not int or value["version"] != 1
            or value["mode"] != "hosted-whole-method" or value["profile"] != _HOSTED_PROFILE
            or not _method_manifest_equal(value["profile_manifest"], _HOSTED_METHOD_IDS)
            or not _method_manifest_equal(value["serial_manifest"], serial)
            or not _method_manifest_equal(value["scheduled_manifest"], scheduled)
            or not _method_manifest_equal(value["launch_manifest"], _hosted_launch_order(scheduled))
            or any(type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0
                   for key in ("work_deadline_epoch", "cleanup_deadline_epoch"))
            or value["cleanup_deadline_epoch"] != value["work_deadline_epoch"] + _HOSTED_CLEANUP_SECONDS
            or any(type(value[key]) is not int for key in ("capacity", "peak_children", "launched", "completed"))
            or not 1 <= value["capacity"] <= 4 or not 0 <= value["peak_children"] <= value["capacity"]
            or value["launched"] != len(scheduled) or value["completed"] != len(scheduled)
            or (bool(scheduled) != bool(value["peak_children"])) or value["cleanup_complete"] is not True
            or value["infrastructure_errors"] != [] or value["timing_basis"] != "observed-parent-wall-and-test-sum"):
        raise RunnerError("invalid hosted execution metadata or incomplete cleanup")
    return (value["mode"], value["profile"], "sum-observed-shard-walls")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run"):
        command = commands.add_parser(name)
        command.add_argument("--id", action="append", default=[])
        command.add_argument("--module", action="append", default=[])
        command.add_argument("--tier", action="append", default=[])
        command.add_argument("--shard", choices=tuple(f"{index}/{suite_policy.SHARD_COUNT}"
                                                    for index in range(1, suite_policy.SHARD_COUNT + 1)),
                             help="one complete partition of the five-shard suite")
        command.add_argument("--json")
        if name == "run":
            command.add_argument("--slowest", type=int, default=20)
            command.add_argument("--failfast", action="store_true")
            command.add_argument("--hosted-methods")
            command.add_argument("--hosted-deadline-epoch", type=float)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("reports", nargs=suite_policy.SHARD_COUNT, metavar="REPORT",
                           help="one current report for each of 1/5 through 5/5")
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
        hosted = _hosted_options(args) if args.command == "run" else None
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
        if hosted is not None:
            report = _hosted_execute(canonical, selected, hosted, slowest=args.slowest, stream=sys.stderr)
            _write_human_report(report, sys.stderr)
            if args.json:
                _write_json(args.json, report)
            return 0 if report["outcome"] == "success" else 1
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
    if len(sys.argv) == 3 and sys.argv[1] == "--_method-supervisor":
        raise SystemExit(_method_supervisor(sys.argv[2]))
    raise SystemExit(main())
