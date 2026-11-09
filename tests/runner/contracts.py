"""Canonical test runner: contracts owner."""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 2


RUNNER_NAME = "story-writing-agents-unittest"


CANONICAL_INVENTORY_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "test_budget_v1.json"


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


@dataclass(frozen=True)
class CanonicalInventory:
    """Expected IDs from the frozen manifest, without a discovery claim."""

    ids: tuple[str, ...]

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(sorted({identifier.rsplit(".", 2)[0] for identifier in self.ids}))
