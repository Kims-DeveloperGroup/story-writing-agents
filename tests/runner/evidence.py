"""Canonical test runner: evidence owner."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import stat
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO
from tests.local_ci_state import LocalCIError, snapshot
from .contracts import RunnerError


def manifest(ids: Sequence[str]) -> dict[str, object]:
    encoded = "".join(f"{identifier}\n" for identifier in ids).encode()
    return {
        "digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "count": len(ids),
        "ordered_ids": list(ids),
    }


def validate_collection_provenance(value, *, require_fresh=False):
    expected = {"kind", "canonical_basis", "fresh_full_discovery"}
    if (type(value) is not dict or set(value) != expected
            or type(value["fresh_full_discovery"]) is not bool):
        raise RunnerError("invalid collection provenance")
    fresh = value["fresh_full_discovery"]
    if (value["kind"] != ("fresh_discovery" if fresh else "targeted")
            or value["canonical_basis"] != ("fresh_discovery" if fresh else "frozen_inventory")
            or require_fresh and not fresh):
        raise RunnerError("complete shards require fresh canonical discovery")


def source_snapshot() -> dict[str, object]:
    try:
        captured_source_inputs = snapshot(Path(__file__).resolve().parents[2])
        captured_source_path = Path(os.environ["RUNNER_TEMP"]) / ("source-input-snapshot-" + str(captured_source_inputs["digest"]).split(":")[-1] + ".json")
        captured_bytecode_metadata = {}
        for captured_name, captured_value in captured_source_inputs["inputs"].items():
            if not captured_name.endswith(".pyc"):
                continue
            captured_cache = Path(__file__).resolve().parents[2] / captured_name
            captured_raw = captured_cache.read_bytes()
            captured_python = captured_cache.parent.parent / (captured_cache.name.split(".cpython-")[0] + ".py")
            captured_python_raw = captured_python.read_bytes()
            captured_bytecode_metadata[captured_name] = {
                "snapshot_record": captured_value,
                "full_sha256": hashlib.sha256(captured_raw).hexdigest(),
                "header_hex": captured_raw[:16].hex(),
                "magic_hex": captured_raw[:4].hex(),
                "flags": int.from_bytes(captured_raw[4:8], "little"),
                "header_timestamp_seconds": int.from_bytes(captured_raw[8:12], "little"),
                "header_source_size": int.from_bytes(captured_raw[12:16], "little"),
                "payload_sha256": hashlib.sha256(captured_raw[16:]).hexdigest(),
                "source_name": str(captured_python.relative_to(Path(__file__).resolve().parents[2])),
                "source_sha256": hashlib.sha256(captured_python_raw).hexdigest(),
                "source_mtime_seconds": int(captured_python.stat().st_mtime),
                "source_bytes": len(captured_python_raw),
            }
        captured_source_path.write_text(json.dumps({"snapshot": captured_source_inputs, "bytecode_metadata": captured_bytecode_metadata}, sort_keys=True), encoding="utf-8")
        return captured_source_inputs
    except LocalCIError as error:
        raise RunnerError(f"cannot capture test source: {error}") from error


def execution_environment() -> dict[str, str]:
    records = []
    for item in importlib.metadata.distributions():
        if type(item) is importlib.metadata.PathDistribution:
            metadata = item.metadata
            records.append((str(metadata.get("Name", "")).lower(), metadata["Version"]))
        else:
            records.append((str(item.metadata.get("Name", "")).lower(), item.version))
    distributions = sorted(records)
    encoded = json.dumps(distributions, separators=(",", ":")).encode()
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "executable": sys.executable, "platform": platform.platform(),
        "system": platform.system(), "machine": platform.machine(),
        "dependency_lock_sha256": hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "uv.lock").read_bytes()).hexdigest(),
        "installed_distributions_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def write_json(path: str, payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path == "-":
        sys.stdout.write(rendered)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_NOFOLLOW", "O_CLOEXEC"):
        flags |= int(getattr(os, name, 0))
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("JSON evidence destination is not a regular file")
        if hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise OSError("JSON evidence destination is not owned by this user")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        os.set_inheritable(descriptor, False)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            descriptor = -1
            stream.write(rendered)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_json_object(path: str) -> dict[str, object]:
    descriptor = -1
    try:
        inspected = os.lstat(path)
        if not stat.S_ISREG(inspected.st_mode):
            raise OSError("shard report is not a regular file")
        if hasattr(os, "geteuid") and inspected.st_uid != os.geteuid():
            raise OSError("shard report is not owned by this user")
        flags = os.O_RDONLY
        for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"):
            flags |= int(getattr(os, name, 0))
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("opened shard report is not a regular file")
        if hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise OSError("opened shard report is not owned by this user")
        if (inspected.st_dev, inspected.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("shard report identity changed while opening")
        os.set_inheritable(descriptor, False)
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as stream:
            descriptor = -1
            payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunnerError(f"cannot read shard report {path!r}: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if type(payload) is not dict:
        raise RunnerError(f"shard report {path!r} must be a JSON object")
    return payload


def required_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if type(value) is not dict:
        raise RunnerError(f"shard report has invalid {key!r} metadata")
    return value


def required_durations(payload: dict[str, object]) -> dict[str, float]:
    durations = required_dict(payload, "durations")
    expected_keys = {"wall_seconds", "cumulative_test_seconds"}
    if set(durations) != expected_keys:
        raise RunnerError("shard report has invalid 'durations' fields")
    validated: dict[str, float] = {}
    for key in sorted(expected_keys):
        value = durations[key]
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            normalized = -1.0
        if (
            type(value) not in (int, float)
            or not math.isfinite(normalized)
            or normalized < 0
        ):
            raise RunnerError(
                f"shard report has invalid nonnegative duration {key!r}"
            )
        validated[key] = normalized
    return validated


def required_ids(payload: dict[str, object], key: str) -> tuple[str, ...]:
    recorded_manifest = required_dict(payload, key)
    ids = recorded_manifest.get("ordered_ids")
    if (type(recorded_manifest.get("count")) is not int or type(recorded_manifest.get("digest")) is not str
            or type(ids) is not list or not ids or any(type(item) is not str or not item for item in ids)
            or len(set(ids)) != len(ids) or recorded_manifest != manifest(ids)):
        raise RunnerError(f"shard {key} is invalid")
    return tuple(ids)


def required_report_identity(report: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    source = required_dict(report, "source_binding")
    keys = {"checkout_sha", "checkout_tree", "before_digest", "after_digest", "source_valid"}
    if set(source) != keys or source["source_valid"] is not True:
        raise RunnerError("shard source binding is invalid or stale")
    for key in ("checkout_sha", "checkout_tree"):
        value = source[key]
        if type(value) is not str or len(value) != 40 or set(value) - set("0123456789abcdef"):
            raise RunnerError("shard source commit/tree is invalid")
    digest = source["before_digest"]
    if (type(digest) is not str or not digest.startswith("sha256:") or len(digest) != 71
            or set(digest[7:]) - set("0123456789abcdef") or source["after_digest"] != digest):
        raise RunnerError("shard source changed during execution")
    environment = required_dict(report, "environment")
    common = ("python_version", "python_implementation", "system", "machine",
              "dependency_lock_sha256", "installed_distributions_sha256")
    if (set(environment) != set(common) | {"executable", "platform"}
            or any(type(value) is not str or not value for value in environment.values())
            or report.get("environment_valid") is not True):
        raise RunnerError("shard execution environment is invalid or changed")
    for key in ("dependency_lock_sha256", "installed_distributions_sha256"):
        value = environment[key]
        if len(value) != 64 or set(value) - set("0123456789abcdef"):
            raise RunnerError("shard environment fingerprint is invalid")
    return source, {key: environment[key] for key in common}
