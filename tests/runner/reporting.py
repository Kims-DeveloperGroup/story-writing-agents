"""Canonical test runner: reporting owner."""
from __future__ import annotations

from .evidence import validate_collection_provenance
import json
import math
import os
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO
from tests import suite_policy
from tests.local_ci_state import LocalCIError, snapshot
from .contracts import CANONICAL_INVENTORY_PATH, CanonicalCollection, RUNNER_NAME, RunnerError, SCHEMA_VERSION
from .selection import suite_policy_metadata
from .evidence import load_json_object, manifest, required_dict, required_durations, required_ids, required_report_identity
from .execution import Execution
from .profile import METHOD_TOKEN_ENV, execution_metadata


def run_report(canonical: CanonicalCollection, selection: CanonicalCollection,
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
        "canonical_manifest": manifest(canonical.ids),
        "selection_manifest": manifest(selection.ids),
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
    token = os.environ.get(METHOD_TOKEN_ENV)
    if token is not None:
        if len(token) != 32 or set(token) - set("0123456789abcdef"):
            raise RunnerError("invalid method child token")
        report["method_child"] = {"version": 1, "token": token, "pid": os.getpid(), "parent_pid": os.getppid()}
    return report


def write_policy_human(report: dict[str, object], stream: TextIO) -> None:
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


def write_human_report(report: dict[str, object], stream: TextIO) -> None:
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
    write_policy_human(report, stream)
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
        report = load_json_object(path)
        if "collection_provenance" in report:
            validate_collection_provenance(report.get("collection_provenance"), require_fresh=True)
        if (type(report.get("schema_version")) is not int or report.get("schema_version") != SCHEMA_VERSION
                or report.get("runner") != RUNNER_NAME
                or report.get("kind") != "run_result" or report.get("outcome") not in {"success", "failure"}):
            raise RunnerError(f"{path!r} is not a current terminal canonical run report")
        selection = required_dict(report, "selection")
        shard = selection.get("shard")
        if shard not in identities or shard in by_shard or selection.get("tiers") != []:
            raise RunnerError("reports must have complementary unique five-shard identities")
        bindings.append(required_report_identity(report))
        durations = required_durations(report)
        totals = required_dict(report, "totals")
        if any(type(totals.get(key)) is not int or totals[key] < 0 for key in counters):
            raise RunnerError(f"shard {shard} has invalid totals")
        selected = totals["selected"]
        if (selected <= 0 or totals["started"] != selected or totals["completed"] != selected
                or totals["interrupted"] != 0 or report.get("unexecuted_ids") != []):
            raise RunnerError(f"shard {shard} is incomplete")
        selection_ids = required_ids(report, "selection_manifest")
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
    modes = {execution_metadata(report) for report in by_shard.values()}
    if len(modes) != 1:
        raise RunnerError("mixed execution modes, profiles or timing bases")
    mode, profile, timing_basis = modes.pop()
    first = by_shard[identities[0]]
    canonical = required_dict(first, "canonical_manifest")
    canonical_ids = required_ids(first, "canonical_manifest")
    inventory = load_json_object(str(CANONICAL_INVENTORY_PATH)).get("subtest_caps")
    if (type(inventory) is not dict or not inventory
            or any(type(identifier) is not str or not identifier
                   or type(cap) is not int or cap < 0
                   for identifier, cap in inventory.items())):
        raise RunnerError("current frozen canonical inventory is invalid")
    if canonical_ids != tuple(sorted(inventory)):
        raise RunnerError("shard canonical inventory differs from the current frozen inventory")
    policy = required_dict(first, "suite_policy")
    if any(required_dict(report, "canonical_manifest") != canonical
           or required_dict(report, "suite_policy") != policy for report in by_shard.values()):
        raise RunnerError("shard canonical manifests or policy partition metadata disagree")
    try:
        weights = suite_policy.load_timing_weights()
        plan = suite_policy.partition_full_shards(canonical_ids, weights)
    except suite_policy.SuitePolicyError as error:
        raise RunnerError(str(error)) from error
    if json.dumps(policy, sort_keys=True) != json.dumps(suite_policy_metadata(weights, plan), sort_keys=True):
        raise RunnerError("shard policy differs from the current deterministic five-partition plan")
    source = bindings[0][0]
    root = Path(__file__).resolve().parents[2]
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
        ids = required_ids(report, "selection_manifest")
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


def write_reconciliation_human(report: dict[str, object], stream: TextIO) -> None:
    canonical = required_dict(report, "canonical_manifest")
    policy = required_dict(report, "suite_policy")
    partition = required_dict(policy, "partition")
    print(
        "reconciliation: "
        f"outcome={report['outcome']} count={canonical['count']} "
        f"canonical={canonical['digest']} partition={partition['digest']}",
        file=stream,
    )
    durations = required_dict(report, "durations")
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
        totals = required_dict(shard, "totals")
        durations = required_dict(shard, "durations")
        print(
            f"  shard={shard['identity']} outcome={shard['outcome']} "
            f"completed={totals['completed']}/{totals['selected']} "
            f"wall={float(durations['wall_seconds']):.3f}s",
            file=stream,
        )
