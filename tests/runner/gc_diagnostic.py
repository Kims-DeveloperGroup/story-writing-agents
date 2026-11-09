"""CI-only GC observation of existing selections; never a performance certificate."""
from __future__ import annotations

import argparse
from array import array
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from .cli import main as runner_main
from .contracts import RUNNER_NAME, SCHEMA_VERSION
from .evidence import required_report_identity, write_json

SLOW_COLLECTION_NS = 5_000_000
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def gc_state() -> dict:
    return {"enabled": gc.isenabled(), "thresholds": list(gc.get_threshold()),
            "callback_count": len(gc.callbacks)}


def source_sha() -> str:
    value = subprocess.check_output(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"], text=True).strip()
    if len(value) != 40 or set(value) - set("0123456789abcdef"):
        raise ValueError("unexpected checkout identity")
    return value


def current_canonical_id() -> str | None:
    """Keep only an ID string, including when a representative case nests runners."""
    frame = candidate = owner = None
    try:
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code.co_name == "run":
                candidate = frame.f_locals.get("self")
                if isinstance(candidate, unittest.TestCase):
                    owner = candidate
            frame = frame.f_back
        return owner.id() if owner is not None else None
    finally:
        del frame, candidate, owner


class GCObservation:
    """Numeric storage avoids retaining frames, exceptions, or per-event containers."""

    def __init__(self):
        self.starts = [None, None, None]
        self.intervals = array("Q")
        self.slow_cases = {}
        self.errors = 0

    def callback(self, phase, info):
        try:
            stamp = time.perf_counter_ns()
            generation = info.get("generation")
            if type(generation) is not int or generation not in (0, 1, 2):
                self.errors += 1
                return
            if phase == "start":
                if self.starts[generation] is not None:
                    self.errors += 1
                self.starts[generation] = stamp
            elif phase == "stop":
                start = self.starts[generation]
                self.starts[generation] = None
                if start is None or stamp < start:
                    self.errors += 1
                    return
                index = len(self.intervals) // 3
                self.intervals.append(generation)
                self.intervals.append(start)
                self.intervals.append(stamp)
                if stamp - start >= SLOW_COLLECTION_NS:
                    self.slow_cases[index] = current_canonical_id()
            else:
                self.errors += 1
        except BaseException:
            # Never expose callback exception contents or alter the observed case.
            self.errors += 1

    def records(self) -> list[dict]:
        result = []
        for offset in range(0, len(self.intervals), 3):
            generation, start, stop = self.intervals[offset:offset + 3]
            row = {"generation": generation, "start_ns": start,
                   "stop_ns": stop, "duration_ns": stop - start}
            if offset // 3 in self.slow_cases:
                row["canonical_id"] = self.slow_cases[offset // 3]
            result.append(row)
        return result


def report_path(arguments: list[str]) -> Path:
    paths = []
    for index, token in enumerate(arguments):
        if token == "--json" and index + 1 < len(arguments):
            paths.append(arguments[index + 1])
        elif token.startswith("--json="):
            paths.append(token.split("=", 1)[1])
    if not paths or paths[-1] in ("", "-"):
        raise ValueError("diagnostics require a separate runner JSON file")
    return Path(paths[-1]).resolve()


def source_report_valid(path: Path, before: str, after: str, exit_code: int) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    binding, _environment = required_report_identity(payload)
    return (payload.get("schema_version") == SCHEMA_VERSION and payload.get("runner") == RUNNER_NAME
            and payload.get("kind") == "run_result" and payload.get("environment_valid") is True
            and binding.get("source_valid") is True and before == after == binding.get("checkout_sha")
            and binding.get("before_digest") == binding.get("after_digest")
            and type(binding.get("before_digest")) is str
            and payload.get("outcome") in ("success", "failure")
            and exit_code == (0 if payload["outcome"] == "success" else 1))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation-json", required=True)
    parser.add_argument("runner_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.runner_arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    output = Path(args.observation_json).resolve()
    observation = GCObservation()
    owned_callback = observation.callback
    original_callbacks = tuple(gc.callbacks)
    before_state = gc_state()
    summary = {"schema_version": 1, "kind": "gc_diagnostic",
               "diagnostic_not_performance_certificate": True,
               "slow_case_threshold_ns": SLOW_COLLECTION_NS,
               "runner_arguments": arguments, "gc_before": before_state,
               "gc_during": None, "source_sha": None, "source_sha_after": None,
               "runner_exit_code": None, "source_condition_valid": False,
               "invocation_failed": False}
    installed = False
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true" or Path.cwd().resolve() != REPOSITORY_ROOT:
            raise ValueError("GC observation requires the disposable CI checkout")
        if before_state["enabled"] is not True:
            raise ValueError("GC observation requires the existing enabled configuration")
        if not arguments or arguments[0] != "run":
            raise ValueError("GC observation only forwards canonical run requests")
        if any(token.split("=", 1)[0] in ("--hosted-methods", "--_method-supervisor")
               for token in arguments):
            raise ValueError("GC observation requires execution in the observed process")
        run_output = report_path(arguments)
        if run_output == output or output.is_relative_to(REPOSITORY_ROOT) or run_output.is_relative_to(REPOSITORY_ROOT):
            raise ValueError("diagnostic outputs must be distinct files outside the checkout")
        summary["runner_report"] = str(run_output)
        summary["source_sha"] = source_sha()
        gc.callbacks.append(owned_callback)
        installed = True
        summary["gc_during"] = gc_state()
        summary["runner_exit_code"] = runner_main(arguments)
        summary["source_sha_after"] = source_sha()
        summary["source_condition_valid"] = source_report_valid(
            run_output, summary["source_sha"], summary["source_sha_after"], summary["runner_exit_code"])
    except BaseException:
        summary["invocation_failed"] = True
    finally:
        owned_positions = [index for index, callback in enumerate(gc.callbacks) if callback is owned_callback]
        summary["owned_callback_occurrences_at_cleanup"] = len(owned_positions)
        for index in reversed(owned_positions):
            del gc.callbacks[index]
        summary["gc_after"] = gc_state()
        summary["callbacks_restored"] = (len(gc.callbacks) == len(original_callbacks)
                                          and all(left is right for left, right in zip(gc.callbacks, original_callbacks)))
        states = [before_state, summary["gc_during"] or before_state, summary["gc_after"]]
        summary["gc_configuration_unchanged"] = all(
            state["enabled"] == before_state["enabled"] and state["thresholds"] == before_state["thresholds"]
            for state in states)
        summary["callback_counts_valid"] = (installed
            and isinstance(summary["gc_during"], dict)
            and summary["gc_during"]["callback_count"] == before_state["callback_count"] + 1
            and len(owned_positions) == 1)
        summary["callback_errors"] = observation.errors
        summary["unmatched_starts"] = sum(value is not None for value in observation.starts)
        summary["collections"] = observation.records()
        valid = (not summary["invocation_failed"] and summary["source_condition_valid"]
                 and summary["callbacks_restored"] and summary["gc_configuration_unchanged"]
                 and summary["callback_counts_valid"] and observation.errors == 0
                 and summary["unmatched_starts"] == 0)
        summary["diagnostic_valid"] = valid
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(str(output), summary)
    if not summary["diagnostic_valid"]:
        print("GC diagnostic conditions failed; inspect the observation JSON.", file=sys.stderr)
        return 2
    return summary["runner_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
