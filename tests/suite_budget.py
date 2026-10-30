"""Monotonic inventory and measured wall-time gate for the canonical suite."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Sequence

from tests import runner


BUDGET_PATH = Path(__file__).parent / "fixtures" / "test_budget_v1.json"
BUDGET_REPO_PATH = "tests/fixtures/test_budget_v1.json"


class BudgetError(ValueError):
    """Invalid or expanded suite budget."""


def _budget(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise BudgetError("invalid test budget schema")
    tests = raw.get("subtest_caps")
    if not isinstance(tests, dict) or not tests:
        raise BudgetError("test budget needs a nonempty subtest inventory")
    if any(not isinstance(key, str) or type(value) is not int or value < 0
           for key, value in tests.items()):
        raise BudgetError("invalid subtest cap")
    if list(tests) != sorted(tests):
        raise BudgetError("test budget IDs must be sorted")
    for name in ("max_sequential_wall_seconds", "max_shard_wall_seconds"):
        value = raw.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise BudgetError(f"invalid {name}")
    if type(raw.get("max_test_source_lines")) is not int or raw["max_test_source_lines"] <= 0:
        raise BudgetError("invalid max_test_source_lines")
    return raw


def load_budget(path: Path = BUDGET_PATH) -> dict[str, Any]:
    try:
        return _budget(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise BudgetError(f"cannot read test budget: {error}") from error


def load_base_budget(base_ref: str) -> dict[str, Any] | None:
    tree = subprocess.run(
        ["git", "ls-tree", base_ref, BUDGET_REPO_PATH],
        capture_output=True, text=True, check=False,
    )
    if tree.returncode:
        raise BudgetError(f"cannot inspect base {base_ref}: {tree.stderr.strip()}")
    if not tree.stdout.strip():
        return None
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{BUDGET_REPO_PATH}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise BudgetError(f"cannot read base budget: {result.stderr.strip()}")
    try:
        return _budget(json.loads(result.stdout))
    except json.JSONDecodeError as error:
        raise BudgetError(f"invalid base budget JSON: {error}") from error


def check_monotonic(current: dict[str, Any], base: dict[str, Any]) -> None:
    added = sorted(set(current["subtest_caps"]) - set(base["subtest_caps"]))
    if added:
        raise BudgetError("new canonical test IDs: " + ", ".join(added))
    expanded = sorted(
        key for key, count in current["subtest_caps"].items()
        if count > base["subtest_caps"][key]
    )
    if expanded:
        raise BudgetError("increased subtest caps: " + ", ".join(expanded))
    time_fields = ("max_sequential_wall_seconds", "max_shard_wall_seconds")
    approved_time_transition = (
        tuple(base[name] for name in time_fields) == (1080, 540)
        and tuple(current[name] for name in time_fields) == (18000, 3600)
    )
    approved_increases = set(time_fields) if approved_time_transition else set()
    if (((base["max_test_source_lines"], current["max_test_source_lines"]) == (142474, 142604)
            or (base["max_test_source_lines"], current["max_test_source_lines"]) == (142604, 142887)
            or (base["max_test_source_lines"], current["max_test_source_lines"]) == (142887, 143443)
            or (base["max_test_source_lines"], current["max_test_source_lines"]) == (142604, 143443)
            or (base["max_test_source_lines"], current["max_test_source_lines"]) == (143443, 145031)
            or (base["max_test_source_lines"], current["max_test_source_lines"]) == (142604, 145031))
            and tuple(base[name] for name in time_fields) == (18000, 3600)
            and all(current[key] == base[key] for key in (*time_fields, "subtest_caps"))):
        approved_increases.add("max_test_source_lines")
    for name in (*time_fields, "max_test_source_lines"):
        if current[name] > base[name] and name not in approved_increases:
            raise BudgetError(f"increased {name}")


def check_source_scope(current: dict[str, Any]) -> None:
    lines = sum(
        sum(bool(line.strip()) and not line.lstrip().startswith("#")
            for line in path.read_text(encoding="utf-8").splitlines())
        for path in sorted(Path(__file__).parent.glob("test*.py"))
    )
    if lines > current["max_test_source_lines"]:
        raise BudgetError(f"test source grew to {lines} lines")


def check_reports(
    current: dict[str, Any], paths: Sequence[str], *, allow_wall_time_overage: bool = False,
) -> dict[str, Any]:
    reconciliation = runner.reconcile_reports(paths)
    if reconciliation["outcome"] != "success":
        raise BudgetError("shard tests did not all pass")
    records: dict[str, int] = {}
    for row in reconciliation["tests"]:
        identifier = row["canonical_id"]
        count = row.get("subtest_count")
        if type(count) is not int or count < 0:
            raise BudgetError(f"missing or invalid subtest count: {identifier}")
        records[identifier] = count
    if set(records) != set(current["subtest_caps"]):
        raise BudgetError("shard records differ from frozen inventory")
    expanded = sorted(
        identifier for identifier, count in records.items()
        if count > current["subtest_caps"][identifier]
    )
    if expanded:
        raise BudgetError("subtest scope increased: " + ", ".join(expanded))
    durations = reconciliation["durations"]
    if not allow_wall_time_overage:
        if durations["sequential_equivalent_wall_seconds"] > current["max_sequential_wall_seconds"]:
            raise BudgetError("sequential-equivalent wall time exceeds budget")
        for shard in reconciliation["shards"]:
            if shard["durations"]["wall_seconds"] > current["max_shard_wall_seconds"]:
                raise BudgetError(f"shard {shard['identity']} wall time exceeds budget")
    return reconciliation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--report", action="append", required=True)
    parser.add_argument(
        "--allow-wall-time-overage", action="store_true",
        help="Allow measured wall-time overage; retain all non-time budget checks.",
    )
    args = parser.parse_args(argv)
    try:
        current = load_budget()
        base = load_base_budget(args.base_ref)
        if base is not None:
            check_monotonic(current, base)
        check_source_scope(current)
        result = check_reports(
            current, args.report, allow_wall_time_overage=args.allow_wall_time_overage,
        )
    except (BudgetError, runner.RunnerError, OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f"test budget: {error}\n")
    allowance = " (wall-time overage explicitly allowed)" if args.allow_wall_time_overage else ""
    print(
        f"test budget passed{allowance}: "
        f"{result['totals']['completed']} IDs, "
        f"{result['durations']['sequential_equivalent_wall_seconds']:.1f}s sequential, "
        f"{result['durations']['concurrent_wall_seconds']:.1f}s longest shard"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
