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

    def _policies(self):
        return _legacy_policies(self.policy_fixtures)

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

    def _reject_without_mutation(self, root: Path) -> None:
        before = self._snapshot(root)
        with self.assertRaises((WorkspaceFormatError, WorkspaceSecurityError)):
            Workspace(root).require_ready()
        self.assertEqual(self._snapshot(root), before)

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

    def test_custom_legacy_session_fresh_admission_delivers_and_binds_active_policy(self) -> None:
        root = self._initialize("custom-session")
        ready = initialization.provision_session_resources(root, "novel")
        self.assertIs(ready.state, GitSessionResourceState.READY)
        session = ready.identity.worktree_root
        role = session / AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        custom = role.read_bytes() + b"\nSynthetic session preference: explain each causal transition.\n"
        role.write_bytes(custom)
        before = self._snapshot(root, session)
        definitions = self._definitions(session)
        self.assertIn(custom, definitions[PLOT_ARCHITECT].system_prompt_file.content)
        for definition in definitions.values():
            self.assertTrue(definition.runtime_behavior_policy)
            self.assertIn(definition.runtime_behavior_policy, definition.system_prompt_file.content)
        with mock.patch.object(prompts_module, "_RUNTIME_BEHAVIOR_POLICY", prompts_module._RUNTIME_BEHAVIOR_POLICY + b"\nSynthetic policy revision."):
            revised = self._definitions(session)
        self.assertTrue(all(revised[agent].fingerprint != definitions[agent].fingerprint for agent in ROSTER))
        self.assertEqual(self._snapshot(root, session), before)
        adapter = CodexAdapter(
            executable=sys.executable,
            timeout_seconds=1,
            configuration=ConfigManager(Workspace(session).require_ready()).load_configuration_snapshot(),
            explicit_model=None,
            make_temporary_directory=lambda: lambda **kwargs: tempfile.mkdtemp(dir=self.test_root, **kwargs),
        )
        self.addCleanup(adapter.close)
        binding = adapter.bind(definitions[PLOT_ARCHITECT])
        try:
            staged = (binding.staging_directory / "AGENTS.md").read_bytes()
            self.assertIn(definitions[PLOT_ARCHITECT].system_prompt_file.content, staged)
            self.assertIn(custom, staged)
            self.assertEqual(binding.definition.fingerprint, definitions[PLOT_ARCHITECT].fingerprint)
        finally:
            adapter.release(binding)
        self.assertEqual(role.read_bytes(), custom)
        self.assertEqual(self._snapshot(root, session), before)

    def test_legacy_baseline_reaches_all_seven_session_composition_seams(self) -> None:
        root = self._initialize("seven-seams")
        ready = initialization.provision_session_resources(root, "novel")
        self.assertIs(ready.state, GitSessionResourceState.READY)
        session = ready.identity.worktree_root
        self.assertEqual(initialization.provision_session_resources(session, "novel"), ready)
        before = self._snapshot(root, session)
        for entry in (root, session):
            with self.subTest(entry=entry.name):
                self.assertEqual(Workspace(entry).require_ready().root, entry)
                self.assertEqual(initialization.inspect_session_resources(entry, "novel"), ready)
                self.assertEqual(initialization.require_ready_session_resources(entry, "novel"), ready)
                self.assertEqual(initialization.list_session_resources(entry).sessions, (ready,))
                history = initialization.inspect_session_history(entry, "novel")
                self.assertEqual((history.candidates, history.commit_operations, history.checkpoint_operations), ((), (), ()))
                self.assertIsNotNone(initialization.compose_story_commit_service(entry, "novel"))
                self.assertIsNotNone(initialization.compose_session_checkpoint_service(entry, "novel"))
        self.assertEqual(self._snapshot(root, session), before)

    def test_mixed_unknown_and_wrong_git_baseline_policy_sets_are_rejected(self) -> None:
        root = self._initialize("unknown-policies")
        original = self._policies()
        current = {entry.path: entry.content for entry in canonical_initializer_scaffold()}
        plot = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        cases = (
            {plot: current[plot]},
            {plot: original[plot] + b"\nUnsupported main-root preference.\n"},
            {path: current[path] for path in original},
        )
        for changes in cases:
            with self.subTest(changed_paths=tuple(changes)):
                for path, content in changes.items():
                    (root / path).write_bytes(content)
                self._reject_without_mutation(root)
                for path in changes:
                    (root / path).write_bytes(original[path])
        self.assertEqual(Workspace(root).require_ready().root, root)
        current_root = self._initialize("modified-current", legacy=False)
        (current_root / plot).write_bytes(current[plot] + b"\nUnsupported current main change.\n")
        self._reject_without_mutation(current_root)

    def test_known_policy_does_not_bypass_marker_or_other_immutable_files(self) -> None:
        root = self._initialize("immutable-gates")
        for path, invalid in (
            (WORKSPACE_MARKER_PATH, b'{"schema_version":999}\n'),
            ("core_documents/global_outline.md", b"Uncommitted synthetic outline.\n"),
            (".gitignore", b"*\n"),
        ):
            with self.subTest(path=path):
                target = root / path
                content = target.read_bytes()
                target.write_bytes(invalid)
                self._reject_without_mutation(root)
                target.write_bytes(content)
        self.assertEqual(Workspace(root).require_ready().root, root)

    def test_known_policy_does_not_bypass_index_or_git_topology(self) -> None:
        root = self._initialize("git-gates")
        outline = root / "core_documents/global_outline.md"
        outline.write_bytes(b"Staged synthetic outline.\n")
        self.run_git(root, "add", "--", "core_documents/global_outline.md")
        outline.write_bytes(b"")
        self._reject_without_mutation(root)
        self.run_git(root, "read-tree", "HEAD")
        self.run_git(root, "symbolic-ref", "HEAD", "refs/heads/unexpected")
        # HEAD is deliberately unborn, so capture the namespace without rev-parse HEAD.
        before = self._snapshot()
        with self.assertRaises(WorkspaceFormatError):
            Workspace(root).require_ready()
        self.assertEqual(self._snapshot(), before)

    def test_unsafe_required_policy_paths_fail_without_following_or_repairing(self) -> None:
        root = self._initialize("unsafe-policy")
        target = root / AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        content = target.read_bytes()
        outside = self.test_root / "outside-policy"
        outside.write_bytes(content)
        for kind in ("missing", "symlink", "directory"):
            with self.subTest(kind=kind):
                target.unlink()
                if kind == "symlink":
                    target.symlink_to(outside)
                elif kind == "directory":
                    target.mkdir()
                self._reject_without_mutation(root)
                self.assertEqual(outside.read_bytes(), content)
                if kind == "directory":
                    target.rmdir()
                elif target.is_symlink():
                    target.unlink()
                target.write_bytes(content)

    def test_policy_selection_and_full_projection_read_failures_release_resources(self) -> None:
        root = self._initialize("policy-read-failures")
        selected = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        original = initialization._read_relative_regular
        # The first read selects a known set; the later full projection must
        # independently succeed. Neither failed read may leave a ready instance.
        for fail_on in (1, 2):
            calls = 0
            def fail_read(descriptor, relative):
                nonlocal calls
                if relative == selected:
                    calls += 1
                    if calls == fail_on:
                        raise OSError(errno.EIO, "synthetic required-policy read failure")
                return original(descriptor, relative)
            with self.subTest(fail_on=fail_on):
                before = self._snapshot(root)
                with mock.patch.object(initialization, "_read_relative_regular", side_effect=fail_read):
                    with self.assertRaisesRegex(WorkspaceFormatError, "scaffold requires recovery"):
                        Workspace(root).require_ready()
                self.assertEqual(calls, fail_on)
                self.assertEqual(self._snapshot(root), before)
                self.assertEqual(Workspace(root).require_ready().root, root)

    def test_main_root_replacement_during_session_policy_read_fails_closed(self) -> None:
        root = self._initialize("root-identity")
        ready = initialization.provision_session_resources(root, "novel")
        displaced = self.test_root / "displaced-root"
        selected = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        original = initialization._read_relative_regular
        swapped = False
        def replace_root(descriptor, relative):
            nonlocal swapped
            content = original(descriptor, relative)
            if relative == selected and not swapped:
                swapped = True
                root.rename(displaced)
                root.mkdir()
            return content
        try:
            with mock.patch.object(initialization, "_read_relative_regular", side_effect=replace_root):
                with self.assertRaises(WorkspaceSecurityError):
                    initialization.inspect_session_resources(ready.identity.worktree_root, "novel")
            self.assertTrue(swapped)
            self.assertEqual(tuple(root.iterdir()), ())
        finally:
            if swapped:
                root.rmdir()
                displaced.rename(root)
        self.assertEqual(initialization.require_ready_session_resources(root, "novel"), ready)

    def test_legacy_incomplete_journal_is_not_adopted_and_current_recovery_stays_current(self) -> None:
        original = initialization._install_missing_scaffold
        def fail_after_scaffold(descriptor, scaffold):
            original(descriptor, scaffold)
            raise OSError("synthetic initialization interruption")
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                root = self.test_root / ("partial-legacy" if legacy else "partial-current")
                scaffold = self._scaffold() if legacy else canonical_initializer_scaffold()
                with (
                    mock.patch.object(initialization, "canonical_initializer_scaffold", return_value=scaffold),
                    mock.patch.object(initialization, "_install_missing_scaffold", side_effect=fail_after_scaffold),
                ):
                    with self.assertRaisesRegex(OSError, "synthetic initialization interruption"):
                        Workspace(root).initialize()
                before = self._snapshot()
                if legacy:
                    with self.assertRaisesRegex(WorkspaceFormatError, "journal is invalid"):
                        Workspace(root).initialize()
                    self.assertEqual(self._snapshot(), before)
                    # A current digest cannot authorize legacy partial bytes either.
                    (root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).write_bytes(encode_initialization_journal(WorkspaceInitializationJournal(_CURRENT_MANIFEST)))
                    before = self._snapshot()
                    with self.assertRaisesRegex(WorkspaceFormatError, "partial scaffold requires recovery"):
                        Workspace(root).initialize()
                    self.assertEqual(self._snapshot(), before)
                else:
                    Workspace(root).initialize()
                    self.assertFalse((root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).exists())
                    self.assertEqual(Workspace(root).require_ready().root, root)


if __name__ == "__main__":
    unittest.main()


class PublishedObjectiveWorkspacePolicyCompatibilityTests(WorkspacePolicyCompatibilityTests):
    """Apply the same strict readiness/security controls to the published objective baseline."""

    policy_fixtures = _FIXTURES / "published_2e9"


class CompletePolicySetCompatibilityTests(unittest.TestCase):
    def test_four_complete_sets_admit_and_mixed_or_unknown_sets_preserve_bytes(self):
        case = WorkspacePolicyCompatibilityTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        fixtures = (_FIXTURES, _FIXTURES / "published_2e9", _FIXTURES / "published_main_1e482fe", None)
        roots = []
        sets = []
        for index, fixture in enumerate(fixtures):
            case.policy_fixtures = fixture or _FIXTURES
            root = case._initialize(f"complete-{index}", legacy=fixture is not None)
            policies = {path: (root / path).read_bytes() for path in _legacy_policies()}
            before = case._snapshot(root)
            self.assertEqual(Workspace(root).require_ready().root, root)
            self.assertEqual(set(case._definitions(root)), set(ROSTER))
            self.assertEqual(case._snapshot(root), before)
            roots.append(root)
            sets.append(policies)
        self.assertEqual(len({tuple(sorted(policy.items())) for policy in sets}), 4)
        lead_path = AGENT_SPECS[ROSTER[0]].system_prompt_file
        for index, root in enumerate(roots):
            original = sets[index][lead_path]
            for other in range(4):
                if other == index:
                    continue
                with self.subTest(base=index, mixed_from=other):
                    (root / lead_path).write_bytes(sets[other][lead_path])
                    case._reject_without_mutation(root)
                    (root / lead_path).write_bytes(original)
            (root / lead_path).write_bytes(original + b"\nUnknown policy extension.\n")
            case._reject_without_mutation(root)
            (root / lead_path).write_bytes(original)
            self.assertEqual(Workspace(root).require_ready().root, root)
