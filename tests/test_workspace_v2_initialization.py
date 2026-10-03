"""Acceptance coverage for strict Git-backed schema-v2 workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentBackend,
    SessionAccessState,
    SessionDurableState,
    Workspace,
)
from story_writing_agents.errors import (
    ConfigurationError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.initialization import (
    WorkspaceRootInspection,
    WorkspaceRootState,
    initialize_workspace_root,
)
from story_writing_agents.workspace.schema import (
    CANONICAL_CORE_DOCUMENT_PATHS,
    SESSION_BRANCH_PREFIX,
    SESSION_INTERNAL_REF_PREFIX,
    SESSION_WORKTREE_SIBLING_SUFFIX,
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WORKSPACE_MARKER_PATH,
    WorkspaceInitializationJournal,
    WorkspaceSessionTopology,
    WorkspaceV2Marker,
    canonical_initializer_scaffold,
    decode_initialization_journal,
    decode_workspace_marker,
    encode_initialization_journal,
    encode_workspace_marker,
)

from tests.git_contract_fixture import DisposableGitTestCase, exception_graph
from tests.helpers import (
    materialize_pristine_initialized_workspace,
    workspace_git_identity,
)


_IDENTITY_ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "Workspace Test Author",
    "GIT_AUTHOR_EMAIL": "workspace-author@example.invalid",
    "GIT_COMMITTER_NAME": "Workspace Test Committer",
    "GIT_COMMITTER_EMAIL": "workspace-committer@example.invalid",
}

_MARKER_BYTES = (
    b"{\n"
    b'  "schema_version": 2,\n'
    b'  "session_topology": {\n'
    b'    "branch_prefix": "story/session/",\n'
    b'    "internal_ref_prefix": '
    b'"refs/story-writing-agents/runtime/sessions/",\n'
    b'    "worktree_sibling_suffix": ".story-worktrees"\n'
    b"  }\n"
    b"}\n"
)

_EXPECTED_DIGESTS = {
    ".gitignore": "d2a04d1ee9e09537a6505f58bd322273a6267ea8bf40daa43f7d7dd095d63f6c",
    "agent_states/character_custodian_state/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "agent_states/execution_specialist_state/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "agent_states/orchestrator_state/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "agent_states/orchestrator_state/conversation_defaults/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "agent_states/orchestrator_state/sessions/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "agent_states/plot_architect_state/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "config/agent_configs.json": "16b585aa159899eb54f2f75d013e4dba1c35435f1beef4e34d50862500a4908e",
    "config/agents/character_custodian/AGENTS.md": "806db0bae145e2ba50e920174299de06c20c083e45bba04b76ffe9c2d550e923",
    "config/agents/common/AGENTS.md": "9cf89bafdc5a49073f57773978628b752217b3e1ddd68f379440baf7493b4a4b",
    "config/agents/execution_specialist/AGENTS.md": "4a3ead3e2389bda9662989d02bb8817ad1ec2d7b4d4f322b246b892545d3979b",
    "config/agents/lead_orchestrator/AGENTS.md": "5278b90ba5939db6ee31d4e2e4808ba583b66ebb25614acaf8a79200b9e7ea7a",
    "config/agents/plot_architect/AGENTS.md": "e59aba2b2e750c7b9ef00b2c3f1ea4e7e9b2889f7abf25a840d7db781d5d93e7",
    "config/agents/specialist_collaboration/AGENTS.md": "4ab83f3792a2598a174e6bfb02511162e26f963b5fe73c661afcfdaa3a5afcc3",
    "config/persona_templates/alternative_templates/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "config/persona_templates/default_kim_eun_hee.md": "fd1c91425394d07b308b3869c56579a760dc868568b6c6cfc637148a5f3336d1",
    "config/system_defaults.json": "539426a048ec074569c11e206e07fa145a69da393fc9af711f610642bad0e6df",
    "config/workspace.json": "270108393bbd7867fd7e8c580c1a21105c3a239b7d8888217dcaceee7975b2c1",
    "core_documents/character_sheets.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "core_documents/global_outline.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "core_documents/narrative_rules.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "core_documents/part_outlines.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "debug_logs/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "discussion_logs/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "manuscript/.gitkeep": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


def _manifest_digest() -> str:
    payload = json.dumps(
        [
            {"content_digest": digest, "path": path}
            for path, digest in _EXPECTED_DIGESTS.items()
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _journal_bytes() -> bytes:
    return (
        json.dumps(
            {
                "manifest_digest": _manifest_digest(),
                "schema_version": 2,
                "state": "initializing",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _namespace_snapshot(
    root: Path,
) -> dict[str, tuple[int, int, int, bytes | str | None]]:
    if not root.exists() and not root.is_symlink():
        return {}
    result: dict[str, tuple[int, int, int, bytes | str | None]] = {}
    for path in sorted(root.rglob("*")):
        value = path.lstat()
        content: bytes | str | None = None
        if stat.S_ISREG(value.st_mode):
            content = path.read_bytes()
        elif stat.S_ISLNK(value.st_mode):
            content = os.readlink(path)
        result[path.relative_to(root).as_posix()] = (
            stat.S_IFMT(value.st_mode) | stat.S_IMODE(value.st_mode),
            value.st_size,
            value.st_mtime_ns,
            content,
        )
    return result


def _write_initializing_root(root: Path, *, entry_count: int | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).write_bytes(_journal_bytes())
    entries = canonical_initializer_scaffold()
    selected = entries if entry_count is None else entries[:entry_count]
    for entry in selected:
        target = root / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(entry.content)


class WorkspaceV2SchemaTests(unittest.TestCase):
    """Prove exact pure marker, journal, scaffold, and naming contracts."""

    def test_marker_codec_is_exact_canonical_strict_and_deeply_validated(self) -> None:
        marker = WorkspaceV2Marker()
        self.assertEqual(encode_workspace_marker(), _MARKER_BYTES)
        self.assertEqual(encode_workspace_marker(marker), _MARKER_BYTES)
        self.assertEqual(decode_workspace_marker(_MARKER_BYTES), marker)

        malformed = (
            b"{}\n",
            _MARKER_BYTES[:-1],
            _MARKER_BYTES + b" ",
            _MARKER_BYTES.replace(b'  "schema_version": 2,\n', b""),
            _MARKER_BYTES.replace(
                b'  "schema_version": 2,\n',
                b'  "schema_version": 2,\n  "schema_version": 2,\n',
            ),
            _MARKER_BYTES.replace(
                b'  "schema_version": 2,\n',
                b'  "extra": null,\n  "schema_version": 2,\n',
            ),
            _MARKER_BYTES.replace(b'"schema_version": 2', b'"schema_version": true'),
            _MARKER_BYTES.replace(b"story/session/", b"other/"),
            b"\xff",
        )
        for payload in malformed:
            with self.subTest(payload=payload[:40]):
                with self.assertRaises(WorkspaceFormatError):
                    decode_workspace_marker(payload)
        for version in (1, 3, -1):
            with self.subTest(version=version):
                payload = _MARKER_BYTES.replace(
                    b'"schema_version": 2',
                    f'"schema_version": {version}'.encode(),
                )
                with self.assertRaisesRegex(
                    WorkspaceVersionUnsupportedError,
                    "migration and compatibility.*create a new workspace",
                ):
                    decode_workspace_marker(payload)

        object.__setattr__(marker.session_topology, "branch_prefix", "../forged")
        with self.assertRaises(WorkspaceFormatError):
            encode_workspace_marker(marker)

    def test_journal_codec_is_exact_canonical_strict_and_deeply_validated(self) -> None:
        journal = WorkspaceInitializationJournal(_manifest_digest())
        expected = _journal_bytes()
        self.assertEqual(encode_initialization_journal(journal), expected)
        self.assertEqual(decode_initialization_journal(expected), journal)
        malformed = (
            expected[:-1],
            expected + b" ",
            expected.replace(b'  "state": "initializing"\n', b""),
            expected.replace(
                b'  "state": "initializing"\n',
                b'  "extra": null,\n  "state": "initializing"\n',
            ),
            expected.replace(
                b'  "state": "initializing"\n',
                b'  "state": "initializing",\n  "state": "initializing"\n',
            ),
            expected.replace(b"initializing", b"ready"),
            expected.replace(b"a" if b"a" in expected[:100] else b"0", b"G", 1),
            b"\xff",
        )
        for payload in malformed:
            with self.subTest(payload=payload[:40]):
                with self.assertRaises(WorkspaceFormatError):
                    decode_initialization_journal(payload)
        for version in (1, 3):
            payload = expected.replace(
                b'"schema_version": 2',
                f'"schema_version": {version}'.encode(),
            )
            with self.assertRaises(WorkspaceVersionUnsupportedError):
                decode_initialization_journal(payload)
        object.__setattr__(journal, "state", "forged")
        with self.assertRaises(WorkspaceFormatError):
            encode_initialization_journal(journal)

    def test_scaffold_paths_bytes_digests_and_core_identities_are_exact(self) -> None:
        entries = canonical_initializer_scaffold()
        paths = tuple(entry.path for entry in entries)
        self.assertEqual(paths, tuple(_EXPECTED_DIGESTS))
        self.assertEqual(paths, tuple(sorted(paths, key=lambda path: path.encode())))
        self.assertEqual(len(paths), 25)
        self.assertEqual(
            {
                entry.path: hashlib.sha256(entry.content).hexdigest()
                for entry in entries
            },
            _EXPECTED_DIGESTS,
        )
        contents = {entry.path: entry.content for entry in entries}
        repository_root = Path(__file__).resolve().parents[1]
        for path in (
            "config/agents/common/AGENTS.md",
            "config/agents/specialist_collaboration/AGENTS.md",
            "config/agents/lead_orchestrator/AGENTS.md",
            "config/agents/plot_architect/AGENTS.md",
            "config/agents/character_custodian/AGENTS.md",
            "config/agents/execution_specialist/AGENTS.md",
        ):
            self.assertEqual(contents[path], (repository_root / path).read_bytes())
        self.assertEqual(contents[WORKSPACE_MARKER_PATH], _MARKER_BYTES)
        self.assertEqual(
            tuple(CANONICAL_CORE_DOCUMENT_PATHS),
            (
                "core_documents/character_sheets.md",
                "core_documents/global_outline.md",
                "core_documents/narrative_rules.md",
                "core_documents/part_outlines.md",
            ),
        )
        for path in CANONICAL_CORE_DOCUMENT_PATHS:
            self.assertEqual(contents[path], b"")
        self.assertNotIn(WORKSPACE_INITIALIZATION_JOURNAL_PATH, paths)

    def test_session_topology_policy_is_static_and_identifier_only(self) -> None:
        topology = WorkspaceSessionTopology()
        self.assertEqual(topology.branch_prefix, SESSION_BRANCH_PREFIX)
        self.assertEqual(topology.internal_ref_prefix, SESSION_INTERNAL_REF_PREFIX)
        self.assertEqual(
            topology.worktree_sibling_suffix, SESSION_WORKTREE_SIBLING_SUFFIX
        )
        marker = json.loads(_MARKER_BYTES)
        self.assertNotIn("session_id", marker)
        self.assertNotIn("path", marker)
        self.assertNotIn("worktree", marker["session_topology"])


class WorkspaceV2GitTestCase(DisposableGitTestCase):
    """Provide hermetic real-Git schema-v2 workspace fixtures."""

    def initialization_environment(self) -> dict[str, str]:
        return self.git_environment(**_IDENTITY_ENVIRONMENT)

    def initialize_workspace_fixture(
        self,
        name: str,
        *,
        existing: bool = False,
    ) -> Workspace:
        root = self.test_root / name
        if existing:
            root.mkdir()
        with mock.patch.dict(
            os.environ,
            self.initialization_environment(),
            clear=True,
        ):
            return Workspace(root).initialize()

    def materialize_ready_workspace_fixture(self, name: str) -> Workspace:
        """Copy an already-ready precondition below the guarded Git test root."""

        return materialize_pristine_initialized_workspace(self.test_root / name)

    def assert_exact_baseline(self, root: Path) -> None:
        self.assertEqual(
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            b"refs/heads/main\n",
        )
        self.assertEqual(
            self.run_git(root, "for-each-ref", "--format=%(refname)").stdout,
            b"refs/heads/main\n",
        )
        self.assertEqual(
            self.run_git(root, "rev-list", "--count", "HEAD").stdout, b"1\n"
        )
        self.assertEqual(
            len(
                self.run_git(root, "rev-list", "--parents", "-1", "HEAD").stdout.split()
            ),
            1,
        )
        commit = self.run_git(root, "cat-file", "commit", "HEAD").stdout
        self.assertEqual(commit.split(b"\n\n", 1)[1], b"Initialize story workspace\n")
        listed = tuple(
            path.decode()
            for path in self.run_git(
                root,
                "ls-tree",
                "-r",
                "--name-only",
                "-z",
                "HEAD",
                "--",
            ).stdout.split(b"\x00")
            if path
        )
        indexed = tuple(
            path.decode()
            for path in self.run_git(root, "ls-files", "-z", "--").stdout.split(b"\x00")
            if path
        )
        self.assertEqual(listed, tuple(_EXPECTED_DIGESTS))
        self.assertEqual(indexed, listed)
        self.assertEqual(
            self.run_git(root, "status", "--porcelain=v2", "--").stdout,
            b"",
        )
        self.assertFalse((root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).exists())


class WorkspaceV2InitializationTests(WorkspaceV2GitTestCase):
    """Prove exact fresh, recovery, identity, and ready-root behavior."""

    def test_missing_and_empty_roots_create_exact_clean_sha1_baselines(self) -> None:
        for name, existing in (("missing", False), ("empty", True)):
            with self.subTest(state=name):
                workspace = self.initialize_workspace_fixture(name, existing=existing)
                root = workspace.root
                files = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file() and ".git" not in path.relative_to(root).parts
                }
                self.assertEqual(set(files), set(_EXPECTED_DIGESTS))
                self.assertEqual(
                    {
                        path: hashlib.sha256(content).hexdigest()
                        for path, content in files.items()
                    },
                    _EXPECTED_DIGESTS,
                )
                for path in CANONICAL_CORE_DOCUMENT_PATHS:
                    value = root / path
                    self.assertTrue(value.is_file())
                    self.assertEqual(value.read_bytes(), b"")
                self.assert_exact_baseline(root)
                self.assertEqual(
                    self.run_git(root, "rev-parse", "--show-object-format").stdout,
                    b"sha1\n",
                )
                self.assertEqual(
                    self.run_git(
                        root, "show", "-s", "--format=%an%x00%ae", "HEAD"
                    ).stdout,
                    b"Workspace Test Author\x00workspace-author@example.invalid\n",
                )
                self.assertEqual(
                    self.run_git(
                        root, "show", "-s", "--format=%cn%x00%ce", "HEAD"
                    ).stdout,
                    b"Workspace Test Committer\x00workspace-committer@example.invalid\n",
                )



















class WorkspaceV2RejectionTests(WorkspaceV2GitTestCase):
    """Prove legacy, ambiguous, unsafe, and unexpected states fail closed."""

    def test_legacy_versions_malformed_and_arbitrary_roots_are_nonmutating(
        self,
    ) -> None:
        cases: list[tuple[str, type[Exception]]] = []

        legacy = self.repository("legacy")
        (legacy / "config").mkdir()
        (legacy / "config/system_defaults.json").write_bytes(b"{}\n")
        cases.append(("legacy", WorkspaceVersionUnsupportedError))

        arbitrary = self.repository("arbitrary")
        (arbitrary / "private.bin").write_bytes(b"private\x00bytes")
        cases.append(("arbitrary", WorkspaceFormatError))

        for version in (1, 7):
            root = self.repository(f"version-{version}")
            marker = _MARKER_BYTES.replace(
                b'"schema_version": 2',
                f'"schema_version": {version}'.encode(),
            )
            (root / "config").mkdir()
            (root / WORKSPACE_MARKER_PATH).write_bytes(marker)
            cases.append((root.name, WorkspaceVersionUnsupportedError))

        malformed_marker = self.repository("malformed-marker")
        (malformed_marker / "config").mkdir()
        (malformed_marker / WORKSPACE_MARKER_PATH).write_bytes(
            b'{"schema_version":2}\n'
        )
        cases.append(("malformed-marker", WorkspaceFormatError))

        malformed_journal = self.repository("malformed-journal")
        (malformed_journal / WORKSPACE_INITIALIZATION_JOURNAL_PATH).write_bytes(b"{}\n")
        cases.append(("malformed-journal", WorkspaceFormatError))

        ambiguous = self.repository("ambiguous")
        first = canonical_initializer_scaffold()[0]
        target = ambiguous / first.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(first.content)
        cases.append(("ambiguous", WorkspaceFormatError))

        for name, expected in cases:
            root = self.test_root / name
            before = _namespace_snapshot(root)
            with self.subTest(name=name):
                with self.assertRaises(expected) as caught:
                    Workspace(root)
                self.assertEqual(_namespace_snapshot(root), before)
                if expected is WorkspaceVersionUnsupportedError:
                    message = str(caught.exception).casefold()
                    self.assertIn("migration", message)
                    self.assertIn("compatibility", message)
                    self.assertIn("create a new workspace", message)
                    for retired in ("upgrade", "manual reset", "fallback"):
                        self.assertNotIn(retired, message)





class WorkspaceV2RecoveryTests(WorkspaceV2GitTestCase):
    """Prove durable ordering, interruption recovery, and terminal instances."""


    def test_same_instance_is_terminal_after_scaffold_failure_and_fresh_retry_recovers(
        self,
    ) -> None:
        root = self.test_root / "terminal"
        workspace = Workspace(root)
        original = initialization_module._install_missing_scaffold

        def fail_after_scaffold(descriptor: int, scaffold: object) -> None:
            original(descriptor, scaffold)
            raise OSError("injected scaffold failure")

        with (
            mock.patch.dict(os.environ, self.initialization_environment(), clear=True),
            mock.patch.object(
                initialization_module,
                "_install_missing_scaffold",
                fail_after_scaffold,
            ),
        ):
            with self.assertRaisesRegex(OSError, "injected scaffold failure"):
                workspace.initialize()
        self.assertTrue((root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).is_file())
        with (
            mock.patch.object(initialization_module.os, "open") as opened,
            self.assertRaisesRegex(OSError, "terminal"),
        ):
            workspace.initialize()
        opened.assert_not_called()
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            Workspace(root).initialize()
        self.assert_exact_baseline(root)


class WorkspaceV2InitializationCapabilityTests(unittest.TestCase):
    """Prove secure descriptor capabilities fail before root mutation."""

    def test_missing_descriptor_capabilities_fail_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "workspace"
            workspace = Workspace(root)
            before = tuple(parent.iterdir())

            with (
                mock.patch.object(
                    initialization_module.os,
                    "supports_dir_fd",
                    frozenset(),
                ),
                mock.patch.object(
                    initialization_module.os,
                    "supports_follow_symlinks",
                    frozenset(),
                ),
                workspace_git_identity(),
                self.assertRaises(OSError),
            ):
                workspace.initialize()

            self.assertEqual(tuple(parent.iterdir()), before)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
