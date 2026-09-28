"""Fresh admission and exact Git ownership for the published v2 policy baseline."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.agents import AGENT_SPECS, PLOT_ARCHITECT, ROSTER
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.codex_adapter import CodexAdapter
from story_writing_agents.config import ConfigManager
from story_writing_agents.errors import WorkspaceFormatError, WorkspaceSecurityError
from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.git.session_contracts import GitSessionResourceState
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents import prompts as prompts_module
from story_writing_agents.workspace import initialization
from story_writing_agents.workspace.schema import (
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WORKSPACE_MARKER_PATH,
    WorkspaceInitializationJournal,
    WorkspaceScaffoldEntry,
    canonical_initializer_scaffold,
    encode_initialization_journal,
)

from tests.git_contract_fixture import DisposableGitTestCase


_FIXTURES = Path(__file__).parent / "fixtures/workspace_policy_baselines"
_PUBLISHED_COMMIT = "68a07827924598571ff8e3f6e4a602ad1a52b771"
_CURRENT_MANIFEST = "828f4dbd5ed1da875177976788fb24c9eb2feba467e68a7b462e022afa5cf83d"
_LEGACY_MANIFEST = "c42681a04c910fd3632dc4987148c0664e1e30b77c0bc1e564b40da52745642f"


def _legacy_policies(fixtures: Path = _FIXTURES) -> dict[str, bytes]:
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    return {
        path: (fixtures / row["fixture"]).read_bytes()
        for path, row in manifest["files"].items()
    }


def _legacy_scaffold(fixtures: Path = _FIXTURES) -> tuple[WorkspaceScaffoldEntry, ...]:
    policies = _legacy_policies(fixtures)
    return tuple(
        WorkspaceScaffoldEntry(entry.path, policies.get(entry.path, entry.content))
        for entry in canonical_initializer_scaffold()
    )


def _manifest(scaffold: tuple[WorkspaceScaffoldEntry, ...]) -> GitBaselineManifest:
    return GitBaselineManifest(tuple(
        GitManifestEntry(entry.path, hashlib.sha256(entry.content).hexdigest())
        for entry in scaffold
    ))


class PublishedWorkspacePolicyFixtureTests(unittest.TestCase):
    def test_independent_published_main_baseline_is_exact(self) -> None:
        fixtures = _FIXTURES / "published_main_1e482fe"
        provenance = json.loads((fixtures / "manifest.json").read_text())
        self.assertEqual(provenance["source_commit"], "1e482fe9ed37f94da0e747a8cb54240af2624f5c")
        self.assertEqual(len(provenance["files"]), 6)
        policies = _legacy_policies(fixtures)
        for path, content in policies.items():
            self.assertEqual(hashlib.sha256(content).hexdigest(), provenance["files"][path]["sha256"])
        self.assertNotEqual(policies, _legacy_policies())
        self.assertNotEqual(policies, _legacy_policies(_FIXTURES / "published_2e9"))

    def test_independent_published_objective_baseline_is_exact(self) -> None:
        fixtures = _FIXTURES / "published_2e9"
        provenance = json.loads((fixtures / "manifest.json").read_text())
        self.assertEqual(provenance["source_commit"], "2e9ef969bd07ee71356e91573068876e5c6f2d0d")
        self.assertEqual(len(provenance["files"]), 6)
        for path, content in _legacy_policies(fixtures).items():
            self.assertEqual(hashlib.sha256(content).hexdigest(), provenance["files"][path]["sha256"])
        self.assertEqual(_manifest(_legacy_scaffold(fixtures)).manifest_digest,
                         "59a1cbe0231b02c8fe40844add06bbe6434274e8a9f93cd3a6b8b15324c4d6d4")
        self.assertNotEqual(_legacy_policies(fixtures), _legacy_policies())

    def test_independent_published_bytes_and_current_initializer_are_exact(self) -> None:
        provenance = json.loads((_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["source_commit"], _PUBLISHED_COMMIT)
        policies = _legacy_policies()
        self.assertEqual(len(policies), 6)
        for path, content in policies.items():
            self.assertEqual(hashlib.sha256(content).hexdigest(), provenance["files"][path]["sha256"])
        current = canonical_initializer_scaffold()
        legacy = _legacy_scaffold()
        self.assertEqual(len(current), 25)
        self.assertEqual({a.path for a, b in zip(current, legacy) if a != b}, set(policies))
        self.assertEqual(_manifest(current).manifest_digest, _CURRENT_MANIFEST)
        self.assertEqual(_manifest(legacy).manifest_digest, _LEGACY_MANIFEST)
        repository = Path(__file__).resolve().parents[1]
        for entry in current:
            if entry.path in policies:
                self.assertEqual(entry.content, (repository / entry.path).read_bytes())


class WorkspacePolicyCompatibilityTests(DisposableGitTestCase):
    """Use real disposable Git roots; patch only historical fixture creation."""

    policy_fixtures = _FIXTURES


    def _scaffold(self):
        return _legacy_scaffold(self.policy_fixtures)

    def setUp(self) -> None:
        super().setUp()
        environment = self.git_environment(
            GIT_AUTHOR_NAME="Policy Fixture",
            GIT_AUTHOR_EMAIL="policy@example.invalid",
            GIT_COMMITTER_NAME="Policy Fixture",
            GIT_COMMITTER_EMAIL="policy@example.invalid",
            STORY_RUN_LIVE="0",
            STORY_ROUTING_644_LIVE="0",
            STORY_AUTH631_LIVE="0",
        )
        self.enterContext(mock.patch.dict(os.environ, environment, clear=True))
        # Binding/staging is permitted; a provider invocation is always a defect.
        for adapter in (CodexAdapter, AntigravityAdapter):
            self.enterContext(mock.patch.object(
                adapter, "invoke", side_effect=AssertionError("provider invocation forbidden")
            ))

    def _initialize(self, name: str, *, legacy: bool = True) -> Path:
        root = self.test_root / name
        if legacy:
            # This fixture substitutes independently published bytes only while
            # creating the original baseline, never during the admission under test.
            with mock.patch.object(initialization, "canonical_initializer_scaffold", return_value=self._scaffold()):
                Workspace(root).initialize()
        else:
            Workspace(root).initialize()
        return root

    def _snapshot(self, *roots: Path) -> tuple[dict, dict]:
        namespace = {}
        for path in sorted(self.test_root.rglob("*")):
            info = path.lstat()
            kind = stat.S_IFMT(info.st_mode)
            content = (
                os.readlink(path) if stat.S_ISLNK(info.st_mode)
                else hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode)
                else None
            )
            namespace[path.relative_to(self.test_root).as_posix()] = (
                kind, stat.S_IMODE(info.st_mode), info.st_mtime_ns, content
            )
        git = {}
        for root in roots:
            index = Path(self.run_git(root, "--no-optional-locks", "rev-parse", "--git-path", "index").stdout.decode().strip())
            if not index.is_absolute():
                index = root / index
            git[root.relative_to(self.test_root).as_posix()] = (
                self.run_git(root, "--no-optional-locks", "rev-parse", "HEAD").stdout,
                index.read_bytes(),
            )
        return namespace, git

    def _definitions(self, root: Path) -> dict:
        # A new constructor performs admission; no cached ready Workspace is reused.
        assembler = PromptAssembler(Workspace(root).require_ready())
        return {
            agent: assembler.build_contextual_definition(session_id="policy-session", agent_id=agent)
            for agent in ROSTER
        }


    def test_fresh_legacy_and_current_main_admission_are_isolated_and_read_only(self) -> None:
        legacy = self._initialize("legacy")
        current = self._initialize("current", legacy=False)
        before = self._snapshot(legacy, current)
        fingerprints = {}
        for root in (legacy, current, legacy, current):
            definitions = self._definitions(root)
            observed = {agent: definition.fingerprint for agent, definition in definitions.items()}
            if root in fingerprints:
                self.assertEqual(observed, fingerprints[root])
            fingerprints[root] = observed
            for definition in definitions.values():
                self.assertTrue(definition.runtime_behavior_policy)
                self.assertIn(definition.runtime_behavior_policy, definition.system_prompt_file.content)
        self.assertNotEqual(fingerprints[legacy], fingerprints[current])
        self.assertEqual(self._snapshot(legacy, current), before)











if __name__ == "__main__":
    unittest.main()
