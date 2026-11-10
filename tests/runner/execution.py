"""Canonical test runner: execution owner."""
from __future__ import annotations

import time
import unittest
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TextIO
from .contracts import CanonicalCollection
from .evidence import execution_environment, source_snapshot


def parent_test(test: unittest.TestCase) -> unittest.TestCase:
    return getattr(test, "test_case", test)


class TimedTextTestResult(unittest.TextTestResult):
    """Preserve unittest callbacks while recording parent-test timing and status."""

    STATUS_PRIORITY = {
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
        return parent_test(test).id()

    def _set_status(self, test: unittest.TestCase, status: str) -> None:
        identifier = self._identifier(test)
        current = self._statuses.get(identifier, "running")
        if self.STATUS_PRIORITY[status] >= self.STATUS_PRIORITY[current]:
            self._statuses[identifier] = status

    def _detail(self, test: unittest.TestCase, kind: str, *, error=None, reason=None) -> None:
        parent = parent_test(test)
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
        if parent_test(test) is not test:
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


@dataclass(frozen=True)
class Execution:
    result: TimedTextTestResult
    wall_seconds: float
    interrupted: bool
    source_binding: dict[str, object]
    environment: dict[str, str]
    environment_valid: bool


def execute(selection: CanonicalCollection, *, stream: TextIO, failfast: bool = False,
             clock: Callable[[], float] = time.perf_counter) -> Execution:
    before = source_snapshot()
    environment = execution_environment()
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
    after = source_snapshot()
    binding = {
        "checkout_sha": before["checkout_sha"], "checkout_tree": before["checkout_tree"],
        "before_digest": before["digest"], "after_digest": after["digest"],
        "source_valid": before == after,
    }
    return Execution(result, wall, interrupted, binding, environment,
                     environment == execution_environment())
