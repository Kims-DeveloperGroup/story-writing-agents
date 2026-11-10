"""Canonical test runner: collection owner."""
from __future__ import annotations

import unittest
import json
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO
from .contracts import CanonicalCollection, RunnerError
import tests.runner.contracts as contracts
from .contracts import CanonicalInventory

FRESH_CENSUS_IDS = frozenset({
    "tests.test_suite_budget.SuiteBudgetTests.test_new_id_increased_subtests_and_relaxed_time_are_rejected",
})


def flatten(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise RunnerError(f"unsupported discovered test object: {item!r}")


def canonicalize(
    suite: unittest.TestSuite, *, loader_errors: Sequence[str] = ()
) -> CanonicalCollection:
    if loader_errors:
        raise RunnerError("test collection failed:\n" + "\n".join(loader_errors))
    tests = tuple(flatten(suite))
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


def discover_canonical(
    *,
    loader: unittest.TestLoader | None = None,
    repository_root: Path | None = None,
) -> CanonicalCollection:
    root = repository_root or Path(__file__).resolve().parents[2]
    active_loader = loader or unittest.TestLoader()
    suite = active_loader.discover(
        start_dir=str(root / "tests"),
        pattern="test*.py",
        top_level_dir=str(root),
    )
    return canonicalize(suite, loader_errors=active_loader.errors)


def load_inventory() -> CanonicalInventory:
    payload = json.loads(contracts.CANONICAL_INVENTORY_PATH.read_text(encoding="utf-8"))
    caps = payload.get("subtest_caps")
    if (payload.get("schema_version") != 1 or not isinstance(caps, dict) or not caps
            or list(caps) != sorted(caps)
            or any(type(identifier) is not str or not identifier.startswith("tests.")
                   or type(cap) is not int or cap < 0 for identifier, cap in caps.items())):
        raise RunnerError("invalid frozen canonical inventory")
    if any(identifier not in caps for identifier in FRESH_CENSUS_IDS):
        raise RunnerError("invalid fresh-census prerequisites")
    return CanonicalInventory(tuple(caps))


def load_selected(ids: Sequence[str], loader: unittest.TestLoader | None = None) -> CanonicalCollection:
    """Import only requested owners and reject missing, duplicate, or substituted IDs."""
    active_loader = loader or unittest.TestLoader()
    suite = active_loader.loadTestsFromNames(ids)
    collection = canonicalize(suite, loader_errors=active_loader.errors)
    if collection.ids != tuple(ids):
        raise RunnerError("targeted collection differs from selected frozen IDs")
    return collection
