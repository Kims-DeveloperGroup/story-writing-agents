"""Conservative change-to-owner selection; this module never executes tests."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Sequence

from .contracts import CanonicalInventory, RunnerError
from .evidence import manifest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OWNER_MANIFEST_PATH = REPOSITORY_ROOT / "tests/fixtures/test_owners_v1.json"
MANAGED_NEW_ROOTS = ("src/", "tests/", "config/", ".github/", "tools/")


@dataclass(frozen=True)
class OwnerCatalogue:
    """Validated immutable declarations owned by one selection request."""

    inventory_ids: tuple[str, ...]
    payload: Mapping[str, object]


def normalized_path(value: str, root: Path) -> str:
    if not value or "\x00" in value:
        raise RunnerError("changed paths must be nonempty repository paths")
    path = Path(value)
    if path.is_absolute():
        try:
            value = path.relative_to(root).as_posix()
        except ValueError as error:
            raise RunnerError("changed path is outside the repository") from error
    normalized = PurePosixPath(value)
    if ".." in normalized.parts or normalized.as_posix() in (".", "/"):
        raise RunnerError("changed path is outside the repository")
    return normalized.as_posix()


def git_output(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise RunnerError(f"cannot read changed paths: {result.stderr.strip()}")
    return result.stdout


def changed_paths(root: Path, reference: str) -> tuple[str, tuple[str, ...]]:
    commit = git_output(root, "rev-parse", "--verify", "--end-of-options", reference + "^{commit}").strip()
    paths = []
    for revision_arguments in ((commit, "HEAD", "--"), ("--cached", "HEAD", "--"), ("--",)):
        fields = git_output(root, "diff", "--no-ext-diff", "--name-status", "-z",
                            "--find-renames", *revision_arguments).split("\x00")
        offset = 0
        while offset < len(fields) and fields[offset]:
            status = fields[offset]
            offset += 1
            count = 2 if status[0] in ("R", "C") else 1
            paths.extend(fields[offset:offset + count])
            offset += count
    untracked = git_output(root, "ls-files", "--others", "--exclude-standard", "-z").split("\x00")
    paths.extend(path for path in untracked if path.startswith(MANAGED_NEW_ROOTS))
    return commit, tuple(sorted({normalized_path(path, root) for path in paths}))


def load_owners(inventory: CanonicalInventory, path: Path = OWNER_MANIFEST_PATH) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunnerError(f"cannot read test owner manifest: {error}") from error
    if (type(payload) is not dict or payload.get("schema_version") != 1
            or not isinstance(payload.get("owners"), list)
            or any(type(owner) is not dict for owner in payload["owners"])):
        raise RunnerError("invalid test owner manifest")
    known_ids = set(inventory.ids)
    names = [owner.get("name") for owner in payload["owners"]]
    if any(type(name) is not str or not name for name in names) or len(set(names)) != len(names):
        raise RunnerError("test owner names must be unique")
    for owner in payload["owners"]:
        if (not isinstance(owner.get("paths"), list) or not owner["paths"]
                or any(type(value) is not str or not value or ".." in PurePosixPath(value).parts
                       or value.startswith("/") for value in owner["paths"])
                or not isinstance(owner.get("focused_ids"), list) or not owner["focused_ids"]
                or not isinstance(owner.get("affected_ids"), list)
                or set(owner["focused_ids"]) - set(owner["affected_ids"])
                or set(owner["affected_ids"]) - known_ids
                or set(owner.get("depends_on", ())) - set(names)):
            raise RunnerError(f"invalid test owner: {owner['name']}")
    return payload


def matches(path: str, patterns: Sequence[str]) -> bool:
    return any(path.startswith(pattern) if pattern.endswith("/") else path == pattern
               for pattern in patterns)


def prepare_owners(inventory: CanonicalInventory, path: Path = OWNER_MANIFEST_PATH) -> OwnerCatalogue:
    payload = load_owners(inventory, path)

    def freeze(value):
        if isinstance(value, dict):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    return OwnerCatalogue(inventory.ids, freeze(payload))


def select_impact(inventory: CanonicalInventory, *, paths: Sequence[str] = (),
                  changed_from: str | None = None, scope: str = "focused",
                  root: Path = REPOSITORY_ROOT, owner_path: Path = OWNER_MANIFEST_PATH,
                  catalogue: OwnerCatalogue | None = None) -> dict:
    if scope not in ("focused", "affected"):
        raise RunnerError("unknown impact scope")
    prepared = catalogue if catalogue is not None else prepare_owners(inventory, owner_path)
    if prepared.inventory_ids != inventory.ids:
        raise RunnerError("owner catalogue differs from the selected frozen inventory")
    payload = prepared.payload
    commit, git_paths = changed_paths(root, changed_from) if changed_from is not None else (None, ())
    inputs = tuple(sorted({*git_paths, *(normalized_path(path, root) for path in paths)}))
    ignored = tuple(path for path in inputs if matches(path, payload.get("ignored_paths", ()))
                    or PurePosixPath(path).name in payload.get("ignored_names", ()))
    active = tuple(path for path in inputs if path not in ignored)
    owners = {owner["name"]: owner for owner in payload["owners"]}
    selected_owners = {name for name, owner in owners.items()
                       if any(matches(path, owner["paths"]) for path in active)}
    unknown = tuple(path for path in active if not any(matches(path, owner["paths"])
                                                      for owner in owners.values()))
    expanded = set(selected_owners)
    while True:
        dependents = {name for name, owner in owners.items()
                      if set(owner.get("depends_on", ())) & expanded}
        if dependents <= expanded:
            break
        expanded.update(dependents)
    reason = "unregistered changed path" if unknown else None
    focused_owners = (expanded if any(owners[name].get("boundary") == "existing-cases"
                                     for name in selected_owners) else selected_owners)
    focused = {identifier for name in focused_owners for identifier in owners[name]["focused_ids"]}
    affected = {identifier for name in expanded for identifier in owners[name]["affected_ids"]}
    if unknown:
        focused = affected = set(inventory.ids)
    elif any(owners[name].get("affected_full", False) for name in expanded):
        affected = set(inventory.ids)
        if scope == "affected":
            reason = "shared execution boundary requires complete verification"
    ordered_focused = tuple(identifier for identifier in inventory.ids if identifier in focused)
    ordered_affected = tuple(identifier for identifier in inventory.ids if identifier in affected)
    selected = ordered_focused if scope == "focused" else ordered_affected
    return {
        "scope": scope, "changed_from": commit, "paths": list(inputs),
        "ignored_paths": list(ignored), "owners": sorted(selected_owners),
        "focused_owners": sorted(focused_owners),
        "affected_owners": sorted(expanded), "unregistered_paths": list(unknown),
        "focused_manifest": manifest(ordered_focused), "affected_manifest": manifest(ordered_affected),
        "selected_manifest": manifest(selected), "fallback_reason": reason,
        "reasons": [{"owner": name, "boundary": owners[name].get("boundary", "unspecified"),
                     "reason": "changed owner" if name in selected_owners else "contract consumer"}
                    for name in sorted(expanded)],
    }
