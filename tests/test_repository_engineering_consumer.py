from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / ".codex" / "repository-engineering-kit.lock.json"
START_MARKER = b"<!-- repository-engineering:start -->"
END_MARKER = b"<!-- repository-engineering:end -->"
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")

EXPECTED_TARGETS = {
    ".codex/agents/documentation_maintainer.toml",
    ".codex/agents/modularity_maintainer.toml",
    ".codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md",
    ".codex/agents/regression_diagnostician.toml",
    ".codex/agents/requirements_analyst.toml",
    ".codex/agents/runtime_implementer.toml",
    ".codex/agents/test_engineer.toml",
    "AGENTS.md",
    "AGENT_TASK_RUNBOOK.md",
    "COMMIT_POLICY.md",
    "DEPENDENCY_POLICY.md",
    "GITHUB_WORKFLOW.md",
}

EXPECTED_AGENTS = {
    "documentation_maintainer",
    "modularity_maintainer",
    "regression_diagnostician",
    "requirements_analyst",
    "runtime_implementer",
    "test_engineer",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        "\\" in value
        or path.as_posix() != value
        or path.is_absolute()
        or not path.parts
        or ".." in path.parts
    ):
        raise AssertionError(f"unsafe relative path: {value}")
    return path


class RepositoryEngineeringConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock_bytes = LOCK_PATH.read_bytes()
        cls.lock = json.loads(cls.lock_bytes)

    def test_path_guard_rejects_cross_platform_traversal(self) -> None:
        self.assertEqual(
            _safe_relative_path("skills/repository-engineering-bootstrap/SKILL.md"),
            PurePosixPath("skills/repository-engineering-bootstrap/SKILL.md"),
        )
        for unsafe in ("../asset", "..\\..\\asset", "nested\\asset", "nested//asset"):
            with self.subTest(unsafe=unsafe), self.assertRaises(AssertionError):
                _safe_relative_path(unsafe)

    def test_lock_is_deterministic_and_pins_the_remote_distribution(self) -> None:
        self.assertEqual(
            _sha256(self.lock_bytes),
            "8c95eda2079fc20e4bba6d94b630c71b246c8f1cc1f0a82011316261adb12c03",
        )
        self.assertEqual(
            self.lock_bytes,
            (json.dumps(self.lock, indent=2) + "\n").encode(),
        )
        self.assertEqual(
            list(self.lock),
            [
                "schemaVersion",
                "plugin",
                "installedPluginVersion",
                "pluginSelector",
                "installationSource",
                "installedDistributionManifest",
                "profiles",
                "files",
            ],
        )
        self.assertEqual(self.lock["schemaVersion"], 1)
        self.assertEqual(self.lock["plugin"], "repository-engineering")
        self.assertEqual(self.lock["installedPluginVersion"], "0.2.0")
        self.assertEqual(
            self.lock["pluginSelector"],
            "repository-engineering@repository-engineering-kit",
        )
        self.assertEqual(
            self.lock["installationSource"],
            {
                "kind": "remote",
                "marketplace": "repository-engineering-kit",
                "marketplaceManifest": ".agents/plugins/marketplace.json",
                "marketplaceManifestSha256": (
                    "6e0f44e7cf797eff7cdc73c06b25271f33c49915a1bc37dc0c554df06537b7db"
                ),
                "pluginSource": "plugins/repository-engineering",
                "marketplaceSource": (
                    "https://github.com/Kims-DeveloperGroup/AI-engineering-kit.git"
                ),
                "immutableRef": "2cab4e6a2d0d7f693c4a65a9021622410bf23966",
            },
        )
        self.assertEqual(
            self.lock["installedDistributionManifest"],
            {
                "path": (
                    "skills/repository-engineering-bootstrap/assets/"
                    "distribution-manifest.json"
                ),
                "sha256": (
                    "71696a46bf80bfd733e52cbc9baca62eabfe584df4dc0c61bbf3ac5806b9813c"
                ),
            },
        )
        self.assertEqual(self.lock["profiles"], ["core", "github", "python"])

        source = self.lock["installationSource"]
        self.assertRegex(source["immutableRef"], r"\A[0-9a-f]{40}\Z")
        self.assertRegex(source["marketplaceManifestSha256"], SHA256_RE)
        _safe_relative_path(source["marketplaceManifest"])
        _safe_relative_path(source["pluginSource"])

    def test_all_projection_baselines_are_current_and_safely_contained(self) -> None:
        entries = self.lock["files"]
        targets = [entry["target"] for entry in entries]
        self.assertEqual(targets, sorted(targets))
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(set(targets), EXPECTED_TARGETS)
        self.assertEqual(len(entries), 12)

        for entry in entries:
            asset = _safe_relative_path(entry["asset"])
            target = _safe_relative_path(entry["target"])
            self.assertNotEqual(asset.name, ".mcp.json")
            self.assertRegex(entry["installedAssetSha256"], SHA256_RE)
            self.assertRegex(entry["installedTargetSha256"], SHA256_RE)

            target_path = ROOT.joinpath(*target.parts)
            self.assertTrue(target_path.is_file(), entry["target"])
            self.assertFalse(target_path.is_symlink(), entry["target"])
            target_bytes = target_path.read_bytes()

            if entry["ownership"] == "managed":
                self.assertEqual(
                    set(entry),
                    {
                        "asset",
                        "target",
                        "ownership",
                        "installedAssetSha256",
                        "installedTargetSha256",
                    },
                )
                owned_bytes = target_bytes
            else:
                self.assertEqual(entry["ownership"], "templated")
                self.assertEqual(entry["target"], "AGENTS.md")
                self.assertEqual(entry["startMarker"].encode(), START_MARKER)
                self.assertEqual(entry["endMarker"].encode(), END_MARKER)
                self.assertEqual(target_bytes.count(START_MARKER), 1)
                self.assertEqual(target_bytes.count(END_MARKER), 1)
                start = target_bytes.index(START_MARKER)
                end = target_bytes.index(END_MARKER, start) + len(END_MARKER)
                owned_bytes = target_bytes[start:end]
                exterior = target_bytes[:start] + target_bytes[end:]
                for fact in (
                    b"Kims-DeveloperGroup/story-writing-agents",
                    b"GitHub Project owner: `Kims-DeveloperGroup`",
                    b"GitHub Project number: `1`",
                    b"Independently selected profiles: `core`, `github`, `python`",
                    b"config/agents/",
                ):
                    self.assertIn(fact, exterior)
                    self.assertNotIn(fact, owned_bytes)

            self.assertEqual(_sha256(owned_bytes), entry["installedTargetSha256"])

    def test_development_agent_names_remain_compatible(self) -> None:
        agent_paths = sorted((ROOT / ".codex" / "agents").glob("*.toml"))
        self.assertEqual({path.stem for path in agent_paths}, EXPECTED_AGENTS)
        for path in agent_paths:
            self.assertFalse(path.is_symlink())
            definition = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(definition["name"], path.stem)

    def test_consumer_does_not_vendor_the_canonical_distribution(self) -> None:
        self.assertFalse((ROOT / ".agents" / "plugins" / "marketplace.json").exists())
        self.assertFalse((ROOT / "plugins" / "repository-engineering").exists())
        self.assertFalse((ROOT / "tests" / "test_repository_engineering_plugin.py").exists())
        self.assertFalse(
            any(
                PurePosixPath(entry["asset"]).name == ".mcp.json"
                for entry in self.lock["files"]
            )
        )


if __name__ == "__main__":
    unittest.main()
