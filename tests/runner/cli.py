"""Canonical test runner: cli owner."""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Iterable, Sequence, TextIO
from tests import suite_policy
from tests.local_ci_state import LocalCIError, snapshot
from .contracts import RUNNER_NAME, RunnerError, SCHEMA_VERSION
from .collection import discover_canonical
from .selection import policy_select
from .evidence import manifest, write_json
from .execution import execute
from .reporting import run_report, write_human_report, write_policy_human, write_reconciliation_human, reconcile_reports
from .hosted import hosted_execute, hosted_options


def prepare_selection(args):
    """Keep fresh census authority separate from manifest-first development runs."""
    from .collection import load_inventory, load_selected
    from .contracts import CanonicalInventory, PolicySelection
    from .impact import load_owners, select_impact
    from .selection import selected_ids, suite_policy_metadata, validated_values

    changed = args.changed_from is not None or bool(args.path)
    if args.shard and changed:
        raise RunnerError("--shard cannot be combined with changed-path selectors")
    if args.scope != "focused" and not changed:
        raise RunnerError("--scope requires --changed-from or --path")
    tiers = validated_values(args.tier, "--tier")
    if set(tiers) - {"fast", "full"}:
        raise RunnerError("unknown test tiers: " + ", ".join(sorted(set(tiers) - {"fast", "full"})))
    targeted = changed or args.id or args.module or "fast" in tiers
    if not targeted or args.shard or "full" in tiers:
        canonical = discover_canonical()
        selected = policy_select(canonical, ids=args.id, modules=args.module, tiers=tiers, shard=args.shard)
        if changed:
            selected.metadata["impact_selection"] = select_impact(
                load_inventory(), paths=args.path, changed_from=args.changed_from, scope=args.scope)
        selected.metadata["collection_provenance"] = {
            "kind": "fresh_discovery", "canonical_basis": "fresh_discovery", "fresh_full_discovery": True}
        return canonical, selected
    inventory = load_inventory()
    chosen = set(selected_ids(inventory, ids=args.id, modules=args.module)) if args.id or args.module else set()
    if "fast" in tiers:
        chosen.update(suite_policy.select_fast(inventory.ids))
    metadata = {"selection": {"tiers": list(tiers), "shard": None},
                "suite_policy": suite_policy_metadata(suite_policy.load_timing_weights())}
    impact = select_impact(inventory, paths=args.path, changed_from=args.changed_from,
                           scope=args.scope) if changed else None
    if impact is not None:
        chosen.update(impact["selected_manifest"]["ordered_ids"])
        metadata["impact_selection"] = impact
    ordered = tuple(identifier for identifier in inventory.ids if identifier in chosen)
    prerequisites = set(load_owners(inventory).get("fresh_census_ids", ())) & chosen
    fresh = (impact is not None and impact["fallback_reason"] is not None
             or args.command == "run" and bool(prerequisites))
    metadata["collection_provenance"] = {
        "kind": "fresh_discovery" if fresh else "targeted",
        "canonical_basis": "fresh_discovery" if fresh else "frozen_inventory",
        "fresh_full_discovery": fresh}
    if fresh:
        canonical = discover_canonical()
        if canonical.ids != inventory.ids:
            raise RunnerError("fresh fallback census differs from frozen inventory")
        selected = policy_select(canonical, ids=ordered)
        selected.metadata.update(metadata)
        return canonical, selected
    selected = load_selected(ordered) if ordered and args.command == "run" else CanonicalInventory(ordered)
    return inventory, PolicySelection(selected, metadata)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "run"):
        command = commands.add_parser(name)
        command.add_argument("--id", action="append", default=[])
        command.add_argument("--module", action="append", default=[])
        command.add_argument("--tier", action="append", default=[])
        command.add_argument("--changed-from")
        command.add_argument("--path", action="append", default=[])
        command.add_argument("--scope", choices=("focused", "affected"), default="focused")
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
    args = parser().parse_args(argv)
    try:
        if args.command == "reconcile":
            report = reconcile_reports(args.reports)
            write_reconciliation_human(report, sys.stderr)
            if args.json:
                write_json(args.json, report)
            return 0 if report["outcome"] == "success" else 1
        if args.command == "run" and args.slowest < 0:
            raise RunnerError("--slowest must be zero or greater")
        hosted = hosted_options(args) if args.command == "run" else None
        canonical, selected = prepare_selection(args)
        selection = selected.selection
        if args.command == "list":
            payload = {
                "schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME, "kind": "manifest",
                "canonical_manifest": manifest(canonical.ids),
                "selection_manifest": manifest(selection.ids),
            }
            payload.update(selected.metadata)
            if args.json:
                write_json(args.json, payload)
            else:
                print(*selection.ids, sep="\n")
                selection_manifest = payload["selection_manifest"]
                assert isinstance(selection_manifest, dict)
                print(f"selected: {selection_manifest['count']}")
                print(f"digest: {selection_manifest['digest']}")
                write_policy_human(selected.metadata, sys.stdout)
            return 0
        if not selection.ids:
            payload = {"schema_version": SCHEMA_VERSION, "runner": RUNNER_NAME,
                       "kind": "selection_result", "outcome": "not_run",
                       "reason": "no changed test impact", "selection_manifest": manifest(())}
            payload.update(selected.metadata)
            if args.json:
                write_json(args.json, payload)
            print("No tests selected; no execution or complete-suite result.", file=sys.stderr)
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
            report = hosted_execute(canonical, selected, hosted, slowest=args.slowest, stream=sys.stderr)
            write_human_report(report, sys.stderr)
            if args.json:
                write_json(args.json, report)
            return 0 if report["outcome"] == "success" else 1
        execution = execute(selection, stream=sys.stderr, failfast=args.failfast)
        report = run_report(
            canonical,
            selection,
            execution,
            slowest=args.slowest,
            policy_metadata=selected.metadata,
        )
        write_human_report(report, sys.stderr)
        if args.json:
            write_json(args.json, report)
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
