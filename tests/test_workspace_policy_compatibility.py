"""Fresh admission and exact Git ownership for the published v2 policy baseline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import unittest
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.agents import LEAD_ORCHESTRATOR, ROSTER
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.codex_adapter import CodexAdapter
from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.workspace import initialization
from story_writing_agents.workspace.schema import (
    WorkspaceScaffoldEntry,
    canonical_initializer_scaffold,
)

from tests.git_contract_fixture import DisposableGitTestCase


_FIXTURES = Path(__file__).parent / "fixtures/workspace_policy_baselines"
_PUBLISHED_COMMIT = "68a07827924598571ff8e3f6e4a602ad1a52b771"
_CURRENT_MANIFEST = "85c8eb0c74dc4ce38416bff8bcc0b396f007261eb1140ec1bb22d45cef4ae030"
_PRE_GUIDANCE = _FIXTURES / "pre_guidance_97879d4"
_PRE_EDITORIAL = _FIXTURES / "integrated_794_09db65c"
_PRE_MIXED = _FIXTURES / "integrated_796_76a80f7"
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
    def _published(self, fixtures: Path, source_commit: str) -> dict[str, bytes]:
        provenance = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["source_commit"], source_commit)
        policies = _legacy_policies(fixtures)
        self.assertEqual(len(policies), 6)
        for path, content in policies.items():
            self.assertEqual(hashlib.sha256(content).hexdigest(), provenance["files"][path]["sha256"])
        return policies

    def test_independent_published_main_baseline_is_exact(self) -> None:
        fixtures = _FIXTURES / "published_main_1e482fe"
        policies = self._published(fixtures, "1e482fe9ed37f94da0e747a8cb54240af2624f5c")
        self.assertNotEqual(policies, _legacy_policies())
        self.assertNotEqual(policies, _legacy_policies(_FIXTURES / "published_2e9"))

    def test_independent_published_objective_baseline_is_exact(self) -> None:
        fixtures = _FIXTURES / "published_2e9"
        self._published(fixtures, "2e9ef969bd07ee71356e91573068876e5c6f2d0d")
        self.assertEqual(_manifest(_legacy_scaffold(fixtures)).manifest_digest,
                         "59a1cbe0231b02c8fe40844add06bbe6434274e8a9f93cd3a6b8b15324c4d6d4")
        self.assertNotEqual(_legacy_policies(fixtures), _legacy_policies())

    def test_independent_published_bytes_and_current_initializer_are_exact(self) -> None:
        policies = self._published(_FIXTURES, _PUBLISHED_COMMIT)
        self._published(_PRE_GUIDANCE, "97879d49b095ca37dd4f65ff8ba698569762adba")
        self.assertEqual(_manifest(_legacy_scaffold(_PRE_GUIDANCE)).manifest_digest,
                         "eb7f56021f514b6db7d266ba416e994e2eceb0d6ba977ece97423c1eda1efd85")
        self._published(_PRE_EDITORIAL, "09db65c28ac38130a4e299aeb75a162413cfff46")
        self._published(_PRE_MIXED, "76a80f72be711d927fff7eee4ee455d3b37802c4")
        self.assertEqual(_manifest(_legacy_scaffold(_PRE_EDITORIAL)).manifest_digest,
                         "11a75b3724d164ce94f4df0cde90909516d256ffbfbb007cf0c47ee975fcde2e")
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

    def _initialize(self, name: str, fixtures: Path | None) -> Path:
        root = self.test_root / name
        if fixtures is not None:
            # This fixture substitutes independently published bytes only while
            # creating the original baseline, never during the admission under test.
            with mock.patch.object(initialization, "canonical_initializer_scaffold", return_value=_legacy_scaffold(fixtures)):
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
        fixtures = (_FIXTURES, _FIXTURES / "published_2e9", _FIXTURES / "published_main_1e482fe", _PRE_GUIDANCE, _PRE_EDITORIAL, _PRE_MIXED, None)
        roots = tuple(self._initialize(str(index), fixture) for index, fixture in enumerate(fixtures))
        before = self._snapshot(*roots)
        fingerprints = {}
        for root in (*roots, *roots):
            definitions = self._definitions(root)
            observed = {agent: definition.fingerprint for agent, definition in definitions.items()}
            if root in fingerprints:
                self.assertEqual(observed, fingerprints[root])
            fingerprints[root] = observed
            for definition in definitions.values():
                self.assertTrue(definition.runtime_behavior_policy)
                self.assertIn(definition.runtime_behavior_policy, definition.system_prompt_file.content)
                for component in definition.behavior_policy_components:
                    self.assertEqual(component.content, component.path.read_bytes())
                    self.assertEqual(component.sha256, hashlib.sha256(component.content).hexdigest())
            self.assertIn(b"takes precedence", definitions[LEAD_ORCHESTRATOR].runtime_behavior_policy)
        self.assertEqual(len({tuple(sorted(value.items())) for value in fingerprints.values()}), len(roots))
        self.assertEqual(self._snapshot(*roots), before)











if __name__ == "__main__":
    unittest.main()
