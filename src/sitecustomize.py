"""Instrumented diagnostic actor only; install as src/sitecustomize.py."""
import os as _os
import sys as _sys

_temp_root = _os.environ.get("RUNNER_TEMP")
_profile_path = (
    _os.path.join(_temp_root, "case-profile-current.prof") if _temp_root else None
)
_expected_prefix = [
    "-m", "cProfile", "-o", _profile_path, "-m", "tests.runner", "run",
]
_active = (
    _os.environ.get("GITHUB_ACTIONS") == "true"
    and _os.environ.get("PROFILE_SELECTION") == "true"
    and _profile_path is not None
    and list(getattr(_sys, "orig_argv", ()))[1:8] == _expected_prefix
)

if _active:
    import atexit as _atexit
    import cProfile as _cprofile
    import hashlib as _hashlib
    import json as _json
    import pstats as _pstats
    import tempfile as _tempfile
    import threading as _threading

    _parent_pid = _os.getpid()
    _original_run = _threading.Thread.run
    _lock = _threading.Lock()
    _records = []
    _errors = []
    _recording_failed = False
    _expected_worker = ("concurrent.futures.thread", "_worker")
    try:
        with open(__file__, "rb") as _bootstrap_file:
            _bootstrap_sha = _hashlib.sha256(_bootstrap_file.read()).hexdigest()
    except BaseException as _bootstrap_error:
        _bootstrap_sha = None
        _errors.append(type(_bootstrap_error).__name__)

    def _note_error(error):
        global _recording_failed
        _recording_failed = True
        try:
            with _lock:
                _errors.append(type(error).__name__)
        except BaseException:
            pass

    def _profile_thread_run(self):
        # A fork inherits this wrapper; it must never merge into its parent's
        # artifact. Fresh child interpreters fail the exact orig_argv gate.
        if _os.getpid() != _parent_pid:
            return _original_run(self)
        record = None
        try:
            target = getattr(self, "_target", None)
            module = getattr(target, "__module__", None)
            qualname = getattr(target, "__qualname__", None)
            if (module, qualname) == _expected_worker:
                record = {
                    "module": module,
                    "qualname": qualname,
                    "profile": None,
                    "completed": False,
                }
            del target
            if record is not None:
                with _lock:
                    _records.append(record)
        except BaseException as error:
            _note_error(error)
            return _original_run(self)
        if record is None:
            return _original_run(self)
        if _threading.current_thread() is not self:
            _note_error(RuntimeError())
            return _original_run(self)
        try:
            try:
                record["profile"] = _cprofile.Profile()
            except BaseException as error:
                _note_error(error)
            if record["profile"] is None:
                return _original_run(self)
            return record["profile"].runcall(_original_run, self)
        except BaseException as error:
            # Never replay a target after runcall raises: this may be the
            # original target exception. Preserve that exception unchanged.
            _note_error(error)
            raise
        finally:
            try:
                with _lock:
                    record["completed"] = True
            except BaseException as error:
                _note_error(error)

    def _merge_profiles():
        if _os.getpid() != _parent_pid:
            return
        temp_path = None
        marker = {
            "diagnostic": "thread-profile-bootstrap-v1",
            "instrumented_only": True,
            "normal_performance_eligible": False,
            "valid": False,
            "bootstrap_sha256": _bootstrap_sha,
            "entered": 0,
            "completed": 0,
            "merged_worker_profiles": 0,
            "executor_worker_entered": 0,
            "executor_worker_completed": 0,
            "default_executor_target_visible": False,
            "main_original_sha256": None,
            "merged_sha256": None,
            "errors_types": [],
            "targets": [],
            "limits": "main+executor-workers overlap; not wall/CPU bounds; other threads unprofiled; worker target also serves non-default executors",
        }
        try:
            with _lock:
                records = list(_records)
                errors = list(_errors)
            if _recording_failed and not errors:
                errors.append("InstrumentationError")
            completed = [r for r in records if r["completed"]]
            executor = [r for r in records
                        if (r["module"], r["qualname"]) == _expected_worker]
            profiles = [r["profile"] for r in completed if r["profile"] is not None]
            marker["entered"] = len(records)
            marker["completed"] = len(completed)
            marker["executor_worker_entered"] = len(executor)
            marker["executor_worker_completed"] = sum(r["completed"] for r in executor)
            marker["default_executor_target_visible"] = bool(executor)
            marker.update({
                "entered": len(records),
                "completed": len(completed),
                "executor_worker_entered": len(executor),
                "executor_worker_completed": sum(r["completed"] for r in executor),
                "default_executor_target_visible": bool(executor),
            })
            targets = {}
            for r in records:
                key = (r["module"], r["qualname"])
                counts = targets.setdefault(key, [0, 0])
                counts[0] += 1
                counts[1] += int(r["completed"])
            marker["targets"] = [
                {"module": k[0], "qualname": k[1], "entered": v[0], "completed": v[1]}
                for k, v in targets.items()
            ]
            if len(completed) != len(records) or len(profiles) != len(records):
                errors.append("IncompleteThreadProfiles")
            if not executor or not all(r["completed"] for r in executor):
                errors.append("MissingCompletedExecutorWorker")
            with open(_profile_path, "rb") as main_file:
                marker["main_original_sha256"] = _hashlib.sha256(main_file.read()).hexdigest()
            marker["errors_types"] = sorted(set(errors))
            if errors:
                return
            stats = _pstats.Stats(_profile_path)
            for profile in profiles:
                stats.add(profile)
            # Preserve the original main artifact if dump/replace fails. This
            # temporary is removed, not a new retained workflow artifact.
            fd, temp_path = _tempfile.mkstemp(prefix=".thread-profile-", dir=_temp_root)
            _os.close(fd)
            stats.dump_stats(temp_path)
            _os.replace(temp_path, _profile_path)
            temp_path = None
            marker["merged_worker_profiles"] = len(profiles)
            with open(_profile_path, "rb") as merged_file:
                marker["merged_sha256"] = _hashlib.sha256(merged_file.read()).hexdigest()
            marker["errors_types"] = sorted(set(errors))
            marker["valid"] = not errors and _bootstrap_sha is not None
        except BaseException as error:
            marker["errors_types"] = sorted(set(marker["errors_types"] + [type(error).__name__]))
        finally:
            if temp_path is not None:
                try:
                    _os.unlink(temp_path)
                except BaseException as error:
                    marker["errors_types"].append(type(error).__name__)
                    marker["valid"] = False
            try:
                print(_json.dumps(marker, separators=(",", ":"), sort_keys=True), flush=True)
            except BaseException:
                # Missing marker is invalid qualification, never a target retry.
                pass

    _atexit.register(_merge_profiles)
    _threading.Thread.run = _profile_thread_run
