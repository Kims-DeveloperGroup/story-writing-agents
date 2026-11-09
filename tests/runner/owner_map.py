"""Explicit maintenance of test-import edges; selection never parses the suite."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from .collection import load_inventory
from .impact import OWNER_MANIFEST_PATH, REPOSITORY_ROOT, load_owners, matches


def declared_imports(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            modules.add(node.module)
            modules.update(node.module + "." + alias.name for alias in node.names)
    return modules


def rebuilt_edges(payload: dict, root: Path = REPOSITORY_ROOT) -> dict[str, list[str]]:
    owners = payload["owners"]
    module_paths = {}
    for path in (root / "tests").rglob("*.py"):
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        module_paths[".".join(parts)] = path
    imported = {}

    def providers(module, visited):
        while module not in module_paths and "." in module:
            module = module.rsplit(".", 1)[0]
        if module not in module_paths or module in visited:
            return set()
        visited.add(module)
        name = module_paths[module].relative_to(root).as_posix()
        direct = {owner["name"] for owner in owners if matches(name, owner["paths"])}
        if direct:
            return direct
        # Unregistered helpers still trigger full fallback when changed. Follow
        # their imports here so registered provider changes include their users.
        if module not in imported:
            imported[module] = declared_imports(module_paths[module])
        return {owner for child in imported[module] for owner in providers(child, visited)}

    result = {}
    for owner in owners:
        if owner.get("boundary") != "existing-cases":
            continue
        modules = set()
        for name in owner["paths"]:
            path = root / name
            if path.is_file() and path.suffix == ".py":
                modules.update(declared_imports(path))
        dependencies = {provider for module in modules for provider in providers(module, set())}
        dependencies.discard(owner["name"])
        result[owner["name"]] = sorted(dependencies)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="refresh declared import edges")
    args = parser.parse_args()
    payload = load_owners(load_inventory())
    expected = rebuilt_edges(payload)
    stale = [owner["name"] for owner in payload["owners"] if owner["name"] in expected
             and sorted(owner.get("depends_on", ())) != expected[owner["name"]]]
    if stale and not args.write:
        parser.exit(1, "stale test owner edges: " + ", ".join(stale) + "\n")
    if args.write:
        for owner in payload["owners"]:
            if owner["name"] in expected:
                owner["depends_on"] = expected[owner["name"]]
        OWNER_MANIFEST_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"test owner import edges current: {len(expected)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
