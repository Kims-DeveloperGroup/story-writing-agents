"""Canonical standard-library test discovery and timing evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO


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
                execution: Execution, *, slowest: int) -> dict[str, object]:
    result = execution.result
    records = result.records(selection.ids)
    started_ids = {record["canonical_id"] for record in records}
    statuses = [str(record["status"]) for record in records]
    return {
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


def _write_json(path: str, payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path == "-":
        sys.stdout.write(rendered)
    else:
        Path(path).write_text(rendered, encoding="utf-8")


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run"):
        command = commands.add_parser(name)
        command.add_argument("--id", action="append", default=[])
        command.add_argument("--module", action="append", default=[])
        command.add_argument("--json")
        if name == "run":
            command.add_argument("--slowest", type=int, default=20)
            command.add_argument("--failfast", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run" and args.slowest < 0:
            raise RunnerError("--slowest must be zero or greater")
        canonical = _discover_canonical()
        selection = _select(canonical, ids=args.id, modules=args.module)
        if args.command == "list":
            payload = {
                "schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME, "kind": "manifest",
                "canonical_manifest": _manifest(canonical.ids),
                "selection_manifest": _manifest(selection.ids),
            }
            if args.json:
                _write_json(args.json, payload)
            else:
                print(*selection.ids, sep="\n")
                manifest = payload["selection_manifest"]
                assert isinstance(manifest, dict)
                print(f"selected: {manifest['count']}")
                print(f"digest: {manifest['digest']}")
            return 0
        execution = _execute(selection, stream=sys.stderr, failfast=args.failfast)
        report = _run_report(canonical, selection, execution, slowest=args.slowest)
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
