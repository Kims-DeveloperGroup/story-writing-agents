"""Canonical test runner: hosted owner."""
from __future__ import annotations

from .evidence import validate_collection_provenance
import math
import os
import sys
import time
from pathlib import Path
from .contracts import CANONICAL_INVENTORY_PATH, CanonicalCollection, RUNNER_NAME, RunnerError, SCHEMA_VERSION
from .evidence import execution_environment, load_json_object, manifest, required_durations, required_ids, required_report_identity, source_snapshot
from .execution import TimedTextTestResult, execute
from .profile import HOSTED_CLEANUP_SECONDS, HOSTED_METHOD_IDS, HOSTED_MIXED_SERIAL_IDS, HOSTED_PROFILE, METHOD_TOKEN_ENV, hosted_launch_order
from .reporting import run_report
from .supervision import launch_method, method_receipt


def hosted_capacity() -> int:
    capacity = os.cpu_count()
    if type(capacity) is not int or capacity < 1:
        raise RunnerError("hosted method execution requires known CPU capacity")
    if hasattr(os, "sched_getaffinity"):
        capacity = min(capacity, len(os.sched_getaffinity(0)))
    if capacity < 1:
        raise RunnerError("hosted method execution has no available CPU")
    return min(4, capacity)


def hosted_options(args) -> dict[str, object] | None:
    profile, deadline = args.hosted_methods, args.hosted_deadline_epoch
    if profile is None and deadline is None:
        return None
    if (profile != HOSTED_PROFILE or args.command != "run" or not args.shard
            or args.id or args.module or args.tier or args.failfast
            or type(args.json) is not str or not args.json):
        raise RunnerError("hosted methods require the audited profile and one full shard")
    forbidden = ("STORY_WIRE_TEST_CAPTURE_DIR", METHOD_TOKEN_ENV, "STORY_LOCAL_CI_TOKEN",
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
            "deadline": work_deadline + HOSTED_CLEANUP_SECONDS,
            "work_deadline_epoch": deadline, "cleanup_deadline_epoch": deadline + HOSTED_CLEANUP_SECONDS,
            "report_path": args.json, "capacity": hosted_capacity()}


def hosted_partition(canonical: CanonicalCollection, selection: CanonicalCollection):
    eligible = set(HOSTED_METHOD_IDS)
    mixed_serial = set(HOSTED_MIXED_SERIAL_IDS)
    classes = {identifier.rsplit(".", 1)[0] for identifier in eligible}
    present = {identifier for identifier in canonical.ids if identifier.rsplit(".", 1)[0] in classes}
    if (present != (eligible | mixed_serial) or len(eligible) != 162
            or len(mixed_serial) != 9 or eligible & mixed_serial):
        raise RunnerError("audited whole-method eligibility changed")
    serial = tuple(test for test in selection.tests if test.id() not in eligible)
    scheduled = tuple(identifier for identifier in selection.ids if identifier in eligible)
    return CanonicalCollection(serial, tuple(test.id() for test in serial)), scheduled


def hosted_artifacts(report_path: str) -> Path:
    root = Path(report_path + ".methods")
    root.mkdir(mode=0o700)
    return root


def validate_method_report(report, ids, canonical_ids, binding, environment, caps, exit_code):
    required = {"schema_version", "runner", "kind", "outcome", "canonical_manifest", "selection_manifest",
                "tests", "totals", "unexecuted_ids", "durations", "slowest", "details", "source_binding",
                "environment", "environment_valid"}
    if type(report) is not dict or not required <= set(report) <= required | {'collection_provenance', 'suite_policy', 'selection', 'impact_selection'}:
        raise RunnerError("method report fields differ from the serial contract")
    if "collection_provenance" in report:
        validate_collection_provenance(report.get("collection_provenance"))
    if (report.get("schema_version") != SCHEMA_VERSION or report.get("runner") != RUNNER_NAME
            or report.get("kind") != "run_result" or report.get("outcome") not in ("success", "failure")
            or type(exit_code) is not int or exit_code != (0 if report["outcome"] == "success" else 1)
            or required_ids(report, "canonical_manifest") != tuple(canonical_ids)
            or required_ids(report, "selection_manifest") != tuple(ids) or report.get("unexecuted_ids") != []
            or required_report_identity(report)[0] != binding
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
        if row["status"] != max(["passed", *statuses], key=TimedTextTestResult.STATUS_PRIORITY.__getitem__):
            raise RunnerError("method report status disagrees with details")
    if (totals["passed"] != sum(row["status"] == "passed" for row in rows)
            or (report["outcome"] == "success") != (not any(totals[key] for key in ("failures", "errors", "unexpected_successes")))):
        raise RunnerError("method report outcome disagrees with records")
    durations = required_durations(report)
    if not math.isclose(durations["cumulative_test_seconds"], sum(row["duration_seconds"] for row in rows),
                        rel_tol=1e-12, abs_tol=1e-9):
        raise RunnerError("method report cumulative duration differs")
    return rows, details, totals


def merge_method_reports(canonical, selection, reports, *, binding, environment, caps, wall, metadata, errors, slowest):
    rows, details, totals = {}, [], {key: 0 for key in ("selected", "started", "completed", "passed", "failures",
                                                     "errors", "skipped", "expected_failures", "unexpected_successes", "interrupted")}
    for report, ids, exit_code in reports:
        records, observations, counts = validate_method_report(report, ids, canonical.ids, binding, environment, caps, exit_code)
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
              "outcome": "failure" if failure else "success", "canonical_manifest": manifest(canonical.ids),
              "selection_manifest": manifest(selection.ids), "tests": records, "totals": totals,
              "unexecuted_ids": unexecuted, "durations": {"wall_seconds": wall,
              "cumulative_test_seconds": sum(row["duration_seconds"] for row in records)},
              "slowest": sorted(records, key=lambda row: (-row["duration_seconds"], row["canonical_id"]))[:slowest],
              "details": details, "source_binding": binding, "environment": environment, "environment_valid": True}
    report.update(metadata)
    return report


def hosted_execute(canonical, selected, options, *, slowest, stream):
    import signal
    import subprocess
    serial, scheduled = hosted_partition(canonical, selected.selection)
    caps = load_json_object(str(CANONICAL_INVENTORY_PATH))["subtest_caps"]
    if tuple(sorted(caps)) != canonical.ids or any(type(cap) is not int or cap < 0 for cap in caps.values()):
        raise RunnerError("hosted method inventory differs from frozen caps")
    root = hosted_artifacts(options["report_path"])
    before, environment = source_snapshot(), execution_environment()
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
            execution = execute(serial, stream=stream, source_collection=canonical if isinstance(canonical, CanonicalCollection) else None)
            report = run_report(canonical, serial, execution, slowest=0)
            if execution.interrupted or report["unexecuted_ids"] or report["totals"]["completed"] != len(serial.ids):
                errors.append("serial traversal interrupted or incomplete")
                # The original report already records only observed callbacks.
                reports.append((report, serial.ids, 130))
            else:
                reports.append((report, serial.ids, 0 if report["outcome"] == "success" else 1))
        pending = iter(hosted_launch_order(scheduled))
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
                    process, pipe, config = launch_method(command, directory, work_deadline=work_deadline)
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
                        receipt = method_receipt(config, process)
                        custody_verified = True
                        if process.returncode != 0 or receipt["reason"] != "completed":
                            raise RunnerError("method supervisor did not complete normally")
                        child = load_json_object(str(directory / "report.json"))
                        identity = {"version": 1, "token": config["token"], "pid": receipt["worker_pid"],
                                    "parent_pid": process.pid}
                        captured = child.pop("method_child", None)
                        if (type(captured) is not dict or captured != identity
                                or any(type(captured[name]) is not int for name in ("version", "pid", "parent_pid"))):
                            raise RunnerError("method report is stale or from another child")
                        if child.get("selection") != {"tiers": [], "shard": None}:
                            raise RunnerError("method child must use the original serial ID selector")
                        validate_method_report(child, (identifier,), canonical.ids, binding, environment, caps,
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
                    method_receipt(config, process)
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
    after = source_snapshot()
    environment_valid = environment == execution_environment()
    if before != after or not environment_valid:
        errors.append("hosted method source or environment changed")
    execution_metadata = {"version": 1, "mode": "hosted-whole-method", "profile": HOSTED_PROFILE,
                          "profile_manifest": manifest(HOSTED_METHOD_IDS), "serial_manifest": manifest(serial.ids),
                          "scheduled_manifest": manifest(scheduled),
                 "launch_manifest": manifest(hosted_launch_order(scheduled)),
                 "work_deadline_epoch": options["work_deadline_epoch"],
                 "cleanup_deadline_epoch": options["cleanup_deadline_epoch"],
                 "capacity": capacity, "peak_children": peak,
                          "launched": launched, "completed": completed, "cleanup_complete": cleanup_complete,
                          "timing_basis": "observed-parent-wall-and-test-sum", "infrastructure_errors": errors}
    # Invalid/partial child reports never become synthetic test rows. For an
    # interrupted serial suite retain its already-observed rows separately.
    interrupted = [item for item in reports if item[2] == 130]
    complete = [item for item in reports if item[2] != 130]
    report = merge_method_reports(canonical, selected.selection, complete, binding=binding, environment=environment,
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
