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
    "config/agents/character_custodian/AGENTS.md": "450a04a32e7d5fd27ad52d9a3cb827fd1736cbf875d05a9268de5307c4e61053",
    "config/agents/common/AGENTS.md": "590ffb4541350028b37306f1573d09658dd5ae55ee2c6158cabc57881776d028",
    "config/agents/execution_specialist/AGENTS.md": "e0ea506190029d9998fb737869895134dfa0f7137e25ba0653d3aa701220d09d",
    "config/agents/lead_orchestrator/AGENTS.md": "d92f00a9587af75b3ab386cbb9079d742da1a69d85938642a02dcbf93dee2ba8",
    "config/agents/plot_architect/AGENTS.md": "d34f74f019ce3aad8c5f759b08fadcf1e98bc7debe405c929b895f4f95d30876",
    "config/agents/specialist_collaboration/AGENTS.md": "adb6ab09ff603ff6d8765d3ff3c0bc9b60044180e31e4a2eb658262e8c5fa36c",
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

    def test_sha256_initialized_journal_recovery_converges_when_supported(self) -> None:
        root = self.repository("sha256")
        _write_initializing_root(root)
        initialized = self.run_git(
            root,
            "init",
            "--quiet",
            "--initial-branch=main",
            "--template=",
            "--object-format=sha256",
            check=False,
        )
        if initialized.returncode != 0:
            self.skipTest("installed Git does not support SHA-256 repositories")
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            workspace = Workspace(root).initialize()
        self.assertIsInstance(workspace, Workspace)
        self.assert_exact_baseline(root)
        self.assertEqual(
            self.run_git(root, "rev-parse", "--show-object-format").stdout,
            b"sha256\n",
        )
        self.assertEqual(
            len(self.run_git(root, "rev-parse", "HEAD").stdout.strip()), 64
        )

    def test_missing_identity_is_truthful_terminal_and_fresh_retry_converges(
        self,
    ) -> None:
        root = self.test_root / "identity"
        workspace = Workspace(root)
        with mock.patch.dict(os.environ, self.git_environment(), clear=True):
            with self.assertRaisesRegex(
                ConfigurationError,
                "effective Git identity is unavailable or invalid",
            ) as caught:
                workspace.initialize()
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertTrue((root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).is_file())
        self.assertFalse((root / ".git").exists())
        with self.assertRaisesRegex(OSError, "terminal"):
            workspace.initialize()
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            recovered = Workspace(root).initialize()
        self.assertEqual(recovered.root, root)
        self.assert_exact_baseline(root)

    def test_full_supported_initialization_state_table_converges(self) -> None:
        partial = self.repository("partial")
        _write_initializing_root(partial, entry_count=7)
        marker_only_git_absent = self.repository("marker-complete")
        _write_initializing_root(marker_only_git_absent)
        git_without_baseline = self.repository("git-no-baseline")
        _write_initializing_root(git_without_baseline)
        self.initialize(git_without_baseline)
        ready_with_journal = self.materialize_ready_workspace_fixture(
            "ready-journal"
        ).root
        (ready_with_journal / WORKSPACE_INITIALIZATION_JOURNAL_PATH).write_bytes(
            _journal_bytes()
        )

        for root in (
            partial,
            marker_only_git_absent,
            git_without_baseline,
            ready_with_journal,
        ):
            with self.subTest(root=root.name):
                with mock.patch.dict(
                    os.environ,
                    self.initialization_environment(),
                    clear=True,
                ):
                    workspace = Workspace(root).initialize()
                self.assertEqual(workspace.root, root)
                self.assert_exact_baseline(root)

    def test_clean_ready_reopen_is_byte_index_ref_and_mtime_idempotent(self) -> None:
        workspace = self.materialize_ready_workspace_fixture("reopen")
        root = workspace.root
        fixed_ns = 1_600_000_000_123_456_789
        index = root / ".git" / "index"
        os.utime(index, ns=(fixed_ns, fixed_ns))
        before = _namespace_snapshot(root)
        head = self.run_git(root, "rev-parse", "HEAD").stdout
        index_before = index.read_bytes()
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            self.assertIs(workspace.initialize(), workspace)
            fresh = Workspace(root)
            self.assertIs(fresh.initialize(), fresh)
        self.assertEqual(_namespace_snapshot(root), before)
        self.assertEqual(index.read_bytes(), index_before)
        self.assertEqual(index.stat().st_mtime_ns, fixed_ns)
        self.assertEqual(self.run_git(root, "rev-parse", "HEAD").stdout, head)
        self.assert_exact_baseline(root)

    def test_unknown_untracked_and_ignored_entries_are_opaque_and_preserved(
        self,
    ) -> None:
        cases = {
            "untracked": "private.tmp",
            "ignored": "agent_states/private-provider-state/payload.bin",
        }
        for case, relative in cases.items():
            with self.subTest(case=case):
                root = self.materialize_ready_workspace_fixture(f"opaque-{case}").root
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"private unknown bytes\n")
                before = _namespace_snapshot(root)

                reopened = Workspace(root).require_ready()

                self.assertEqual(reopened.root, root)
                self.assertEqual(_namespace_snapshot(root), before)

    def test_tracked_required_and_staged_unknown_changes_remain_recovery(
        self,
    ) -> None:
        cases = ("tracked-required", "staged-unknown", "committed-unknown")
        for case in cases:
            with self.subTest(case=case):
                root = self.materialize_ready_workspace_fixture(f"dirty-{case}").root
                if case == "tracked-required":
                    target = root / "core_documents/global_outline.md"
                    target.write_bytes(b"private changed prose\n")
                else:
                    target = root / "private-added.txt"
                    target.write_bytes(b"private added bytes\n")
                    self.run_git(root, "add", "--", target.name)
                    if case == "committed-unknown":
                        self.configure_identity(root)
                        self.run_git(
                            root,
                            "commit",
                            "--quiet",
                            "-m",
                            "Add unknown tracked entry",
                        )
                before = _namespace_snapshot(root)
                with self.assertRaises(WorkspaceFormatError) as caught:
                    Workspace(root)
                self.assertEqual(_namespace_snapshot(root), before)
                self.assertIn("recovery", str(caught.exception))
                self.assertNotIn("private changed prose", str(caught.exception))
                self.assertNotIn("private added bytes", str(caught.exception))

    def test_configuration_git_shapes_other_than_unstaged_modify_fail_closed(
        self,
    ) -> None:
        path = "config/system_defaults.json"
        for case in ("staged", "renamed"):
            with self.subTest(case=case):
                root = self.materialize_ready_workspace_fixture(f"config-{case}").root
                defaults = json.loads((root / path).read_text(encoding="utf-8"))
                defaults["default_backend"] = "codex"
                (root / path).write_text(
                    json.dumps(defaults, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if case == "staged":
                    self.run_git(root, "add", "--", path)
                elif case == "renamed":
                    self.run_git(root, "mv", "--", path, "config/renamed.json")
                before = _namespace_snapshot(root)

                with self.assertRaises(WorkspaceFormatError):
                    Workspace(root)

                self.assertEqual(_namespace_snapshot(root), before)

    def test_ready_antigravity_tree_is_never_enumerated_inspected_or_opened(
        self,
    ) -> None:
        root = self.materialize_ready_workspace_fixture("opaque-antigravity").root
        provider = (
            root
            / "agent_states/orchestrator_state/provider_sessions/.antigravity"
        )
        opaque = provider / "sessions/novel/provider/opaque-unreadable"
        opaque.mkdir(parents=True)
        private = opaque / "private.bin"
        private.write_bytes(b"private provider bytes\n")
        outside = self.test_root / "outside-provider-target"
        outside.write_bytes(b"outside provider bytes\n")
        nested_link = provider / "sessions/novel/provider/native-link"
        nested_link.symlink_to(outside)
        native_pipe = provider / "provider-native.pipe"
        os.mkfifo(native_pipe)
        opaque.chmod(0)
        self.addCleanup(opaque.chmod, 0o700)

        selected = (provider, opaque, nested_link, native_pipe, outside)

        def identities() -> tuple[tuple[int, int, int, int], ...]:
            return tuple(
                (
                    stat.S_IFMT(value.st_mode) | stat.S_IMODE(value.st_mode),
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
                for value in (path.lstat() for path in selected)
            )

        before = identities()
        head_before = self.run_git(root, "rev-parse", "HEAD").stdout
        index_before = (root / ".git/index").read_bytes()
        real_stat = initialization_module.os.stat
        real_open = initialization_module.os.open

        def reject_unknown(operation, path, *args, **kwargs):
            try:
                spelling = os.fspath(path)
            except TypeError:
                spelling = ""
            if isinstance(spelling, bytes):
                spelling = os.fsdecode(spelling)
            if any(
                name in spelling
                for name in (
                    ".antigravity",
                    "opaque-unreadable",
                    "native-link",
                    "provider-native.pipe",
                )
            ):
                raise AssertionError(f"readiness {operation} unknown entry")
            return None

        def guarded_stat(path, *args, **kwargs):
            reject_unknown("stated", path, *args, **kwargs)
            return real_stat(path, *args, **kwargs)

        def guarded_open(path, *args, **kwargs):
            reject_unknown("opened", path, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        with (
            mock.patch.object(
                initialization_module,
                "_root_names",
                side_effect=AssertionError("ready root was enumerated"),
            ),
            mock.patch.object(
                initialization_module,
                "_snapshot_relative_files",
                side_effect=AssertionError("ready tree was enumerated"),
            ),
            mock.patch.object(
                initialization_module.os,
                "stat",
                side_effect=guarded_stat,
            ),
            mock.patch.object(
                initialization_module.os,
                "open",
                side_effect=guarded_open,
            ),
        ):
            reopened = Workspace(root).require_ready()

        self.assertEqual(reopened.root, root)
        self.assertEqual(identities(), before)
        self.assertEqual(os.readlink(nested_link), os.fspath(outside))
        self.assertEqual(outside.read_bytes(), b"outside provider bytes\n")
        self.assertEqual(self.run_git(root, "rev-parse", "HEAD").stdout, head_before)
        self.assertEqual((root / ".git/index").read_bytes(), index_before)

    def test_required_path_substitutions_fail_closed_without_target_mutation(
        self,
    ) -> None:
        cases = (
            "missing",
            "directory",
            "symlink",
            "fifo",
            "content",
            "marker-schema",
        )
        for case in cases:
            with self.subTest(case=case):
                root = self.materialize_ready_workspace_fixture(f"required-{case}").root
                target = root / (
                    WORKSPACE_MARKER_PATH
                    if case == "marker-schema"
                    else "core_documents/global_outline.md"
                )
                target.unlink()
                pipe_descriptor = None
                outside = self.test_root / f"outside-required-{case}"
                outside.write_bytes(b"outside required target\n")
                if case == "directory":
                    target.mkdir()
                elif case == "symlink":
                    target.symlink_to(outside)
                elif case == "fifo":
                    os.mkfifo(target)
                    pipe_descriptor = os.open(
                        target,
                        os.O_RDWR | getattr(os, "O_NONBLOCK", 0),
                    )
                elif case == "content":
                    target.write_bytes(b"private required drift\n")
                elif case == "marker-schema":
                    target.write_bytes(b'{"schema_version":2}\n')
                before = _namespace_snapshot(root)

                try:
                    with self.assertRaises(WorkspaceFormatError) as caught:
                        Workspace(root)
                finally:
                    if pipe_descriptor is not None:
                        os.close(pipe_descriptor)

                self.assertEqual(_namespace_snapshot(root), before)
                self.assertEqual(outside.read_bytes(), b"outside required target\n")
                self.assertNotIn("private required drift", str(caught.exception))

    def test_invalid_utf8_mutable_configuration_fails_with_typed_readiness_error(
        self,
    ) -> None:
        root = self.materialize_ready_workspace_fixture("config-invalid-utf8").root
        target = root / "config/system_defaults.json"
        target.write_bytes(b"\xff\xfeprivate-invalid-configuration")
        before = _namespace_snapshot(root)

        with self.assertRaises(WorkspaceFormatError) as caught:
            Workspace(root)

        self.assertEqual(_namespace_snapshot(root), before)
        self.assertNotIn("private-invalid-configuration", str(caught.exception))

    def test_initializer_creates_no_session_branch_ref_or_worktree(self) -> None:
        root = self.initialize_workspace_fixture("no-sessions").root
        self.assertEqual(
            self.run_git(root, "for-each-ref", "--format=%(refname)").stdout,
            b"refs/heads/main\n",
        )
        worktrees = self.run_git(root, "worktree", "list", "--porcelain").stdout
        self.assertEqual(worktrees.count(b"worktree "), 1)
        self.assertFalse(root.parent.joinpath(f"{root.name}.story-worktrees").exists())
        self.assertEqual(
            tuple(
                path.name
                for path in (
                    root / "agent_states/orchestrator_state/sessions"
                ).iterdir()
            ),
            (".gitkeep",),
        )

    def test_consensus_storage_roots_and_dynamic_state_survive_reopen(self) -> None:
        workspace = self.materialize_ready_workspace_fixture("consensus-state")
        root = workspace.root
        material_root = root / "agent_states/orchestrator_state/consensus_materials/v1"
        workflow_root = root / "agent_states/orchestrator_state/consensus_workflows/v2"
        self.assertTrue(material_root.is_dir())
        self.assertTrue(workflow_root.is_dir())

        from story_writing_agents.consensus import (
            ConsensusEvent,
            ConsensusInputKind,
            ConsensusPhase,
            ConsensusStoredWorkflow,
            ConsensusWorkflowSubmission,
            prepare_consensus_phase,
        )
        from story_writing_agents.consensus.contracts import (
            ConsensusExecutionIdentity,
            candidate_material_handle,
        )
        from story_writing_agents.consensus.event_store import ConsensusEventStore

        identity = ConsensusExecutionIdentity("session-1", "conversation-1")
        material = candidate_material_handle("1" * 64)
        request, _ = prepare_consensus_phase(
            owner=self,
            workflow_id="workflow-1",
            phase_request_id="request-1",
            proposal_id="proposal-1",
            execution_identity=identity,
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=material,
            candidate_material=None,
            base_tail_sequence=0,
        )
        submission = ConsensusWorkflowSubmission(
            execution_identity=identity,
            workflow_id="workflow-1",
            proposal_id="proposal-1",
            input_kind=ConsensusInputKind.PROMPT,
            input_material=material,
        )
        ConsensusEventStore(workspace).create_workflow(
            submission,
            ConsensusEvent("request-event", 1, request),
        )
        reopened = Workspace(root)
        self.assertIsInstance(reopened, Workspace)
        loaded = ConsensusEventStore(reopened).load_workflow(
            "session-1", "conversation-1", "workflow-1"
        )
        self.assertEqual(loaded, ConsensusStoredWorkflow(
            submission,
            (ConsensusEvent("request-event", 1, request),),
        ))

    def test_canonical_interactive_runtime_files_survive_reopen(self) -> None:
        root = self.materialize_ready_workspace_fixture("interactive-runtime").root
        session_id = "Session_1"
        conversation_id = "c_" + "a" * 32
        files = {
            "agent_states/orchestrator_state/cli_context.json": b"{}\n",
            f"agent_states/orchestrator_state/conversation_defaults/{session_id}.json": b"{}\n",
            "agent_states/orchestrator_state/provider_sessions/.access-claims.lock": b"",
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json": b"{}\n",
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.access.json": b"claim bytes\n",
            "agent_states/orchestrator_state/provider_sessions/a.access.json": b"short ID\n",
            f"agent_states/orchestrator_state/provider_sessions/{'a' * 64}.access.json": b"maximum ID\n",
            "agent_states/orchestrator_state/provider_sessions/session-2.access.json": b"hyphenated ID\n",
            "config/.conversation_storage.lock": b"",
            f"discussion_logs/{session_id}/interactive/conversations/{conversation_id}/metadata.json": b"{}\n",
            f"discussion_logs/{session_id}/interactive/conversations/{conversation_id}/transcript.jsonl": b"",
            f"discussion_logs/{session_id}/interactive/conversations/{conversation_id}/collaboration-history.jsonl": b"",
            f"discussion_logs/{session_id}/interactive/conversations/{conversation_id}/.collaboration-history.lock": b"",
        }
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        before = _namespace_snapshot(root)
        reopened = Workspace(root).require_ready()

        self.assertEqual(reopened.root, root)
        self.assertEqual(_namespace_snapshot(root), before)
        for relative, content in files.items():
            self.assertEqual((root / relative).read_bytes(), content)

    def test_backend_access_claim_allows_second_session_and_destination_entry(self) -> None:
        workspace = self.materialize_ready_workspace_fixture("backend-access-claim")
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            backend = AgentBackend(workspace)
            self.addCleanup(backend.close)
            claims = {}
            for session_id in ("Session_1", "session-2"):
                resources = backend.provision_session_resources(session_id)
                state = backend.create_session_with_access(session_id)
                self.assertIs(state.durable_state, SessionDurableState.READY)
                self.assertIs(state.access_state, SessionAccessState.HELD_HERE)
                relative = f"agent_states/orchestrator_state/provider_sessions/{session_id}"
                claim_path = workspace.root / f"{relative}.access.json"
                claims[claim_path] = claim_path.read_bytes()
                self.assertEqual(
                    workspace.read_json(f"{relative}.json")["provider_threads"], {},
                )
                before = _namespace_snapshot(workspace.root)
                self.assertEqual(Workspace(workspace.root).require_ready().root, workspace.root)
                destination = Workspace(resources.identity.worktree_root).require_ready()
                self.assertEqual(destination.root, resources.identity.worktree_root)
                self.assertEqual(_namespace_snapshot(workspace.root), before)
                for path, content in claims.items():
                    self.assertEqual(path.read_bytes(), content)

            backend.close()
            for path in claims:
                self.assertFalse(path.exists())
            Workspace(workspace.root).require_ready()

    def test_exact_registered_runtime_owner_paths_survive_reopen(self) -> None:
        root = self.materialize_ready_workspace_fixture("registered-runtime-paths").root
        digest = "a" * 64
        application = "application_1"
        invocation = "invocation_1"
        session = "Session_1"
        files = {
            "debug_logs/.runtime.lock": b"",
            "debug_logs/runtime.jsonl": b"runtime bytes\n",
            "debug_logs/agent_exchanges/.agent_exchanges.lock": b"",
            "debug_logs/agent_exchanges/.state.json": b"{}\n",
            f"debug_logs/agent_exchanges/{session}/lead_orchestrator.jsonl": b"exchange\n",
            f"agent_states/orchestrator_state/context_ledger/v2/{digest}.json": b"{}\n",
            f"agent_states/orchestrator_state/context_ledger/v2/{digest}.lock": b"",
            f"agent_states/orchestrator_state/provider_sessions/.contextual/v1/{session}/{digest}.json": b"{}\n",
            "agent_states/orchestrator_state/writing_artifacts/catalog.json": b"{}\n",
            f"agent_states/orchestrator_state/writing_artifacts/applications/{application}.json": b"{}\n",
            f"agent_states/orchestrator_state/writing_artifacts/input_snapshots/{application}/manifest.json": b"{}\n",
            f"agent_states/orchestrator_state/writing_artifacts/input_snapshots/{application}/operation_events/00000001.json": b"{}\n",
            f"agent_states/orchestrator_state/writing_artifacts/input_snapshots/{application}/invocations/{invocation}/manifest.json": b"{}\n",
            f"agent_states/orchestrator_state/writing_artifacts/input_snapshots/{application}/invocations/{invocation}/events/00000001.json": b"{}\n",
        }
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        reopened = Workspace(root)

        self.assertEqual(reopened.root, root)
        for relative, content in files.items():
            self.assertEqual((root / relative).read_bytes(), content)

    def test_unknown_registered_runtime_owner_shapes_are_opaque(self) -> None:
        digest = "a" * 64
        cases = {
            "debug-unknown": "debug_logs/unknown.jsonl",
            "exchange-agent": "debug_logs/agent_exchanges/session/unknown_agent.jsonl",
            "exchange-nested": "debug_logs/agent_exchanges/session/nested/lead_orchestrator.jsonl",
            "ledger-digest": "agent_states/orchestrator_state/context_ledger/v2/not-a-digest.json",
            "contextual-leaf": f"agent_states/orchestrator_state/provider_sessions/.contextual/v1/session/{digest}.txt",
            "artifact-unknown": "agent_states/orchestrator_state/writing_artifacts/unknown.json",
            "artifact-event-zero": "agent_states/orchestrator_state/writing_artifacts/input_snapshots/application/invocations/invocation/events/00000000.json",
        }
        for name, relative in cases.items():
            with self.subTest(name=name):
                root = self.materialize_ready_workspace_fixture(name).root
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"unexpected\n")
                before = _namespace_snapshot(root)

                reopened = Workspace(root).require_ready()

                self.assertEqual(reopened.root, root)
                self.assertEqual(_namespace_snapshot(root), before)

    def test_malformed_unknown_interactive_paths_are_opaque(self) -> None:
        conversation_id = "c_" + "a" * 32
        cases = {
            "invalid-session": (
                f"discussion_logs/bad session/interactive/conversations/{conversation_id}/metadata.json"
            ),
            "invalid-conversation": (
                "discussion_logs/session/interactive/conversations/bad id/metadata.json"
            ),
            "unknown-leaf": (
                f"discussion_logs/session/interactive/conversations/{conversation_id}/unknown.json"
            ),
            "nested-leaf": (
                f"discussion_logs/session/interactive/conversations/{conversation_id}/nested/metadata.json"
            ),
            "unknown-runtime": "agent_states/orchestrator_state/provider_sessions/session.txt",
            "empty-claim-session": "agent_states/orchestrator_state/provider_sessions/.access.json",
            "dotted-claim-session": "agent_states/orchestrator_state/provider_sessions/session.one.access.json",
            "overlong-claim-session": f"agent_states/orchestrator_state/provider_sessions/{'a' * 65}.access.json",
            "duplicate-claim-suffix": "agent_states/orchestrator_state/provider_sessions/session.access.access.json",
            "wrong-claim-owner": "agent_states/orchestrator_state/conversation_defaults/session.access.json",
        }
        for name, relative in cases.items():
            with self.subTest(name=name):
                root = self.materialize_ready_workspace_fixture(name).root
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"unexpected\n")
                before = _namespace_snapshot(root)

                reopened = Workspace(root).require_ready()

                self.assertEqual(reopened.root, root)
                self.assertEqual(_namespace_snapshot(root), before)


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

    def test_parent_repository_empty_and_journal_roots_reject_before_mutation(
        self,
    ) -> None:
        parent = self.repository("parent")
        self.initialize(parent)
        empty = parent / "empty"
        empty.mkdir()
        empty_before = _namespace_snapshot(empty)
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            with self.assertRaises(WorkspaceSecurityError):
                Workspace(empty).initialize()
        self.assertEqual(_namespace_snapshot(empty), empty_before)

        partial = parent / "partial"
        _write_initializing_root(partial, entry_count=4)
        partial_before = _namespace_snapshot(partial)
        with mock.patch.dict(os.environ, self.initialization_environment(), clear=True):
            with self.assertRaises(WorkspaceSecurityError):
                Workspace(partial).initialize()
        self.assertEqual(_namespace_snapshot(partial), partial_before)

    def test_unsupported_repository_topology_matrix_is_nonmutating(self) -> None:
        scenarios: list[tuple[str, Path]] = []

        bare = self.repository("bare")
        self.run_git(
            bare, "init", "--bare", "--quiet", "--initial-branch=main", "--template="
        )
        scenarios.append(("bare", bare))

        sparse = self.repository("sparse")
        self.initialize(sparse)
        self.run_git(sparse, "config", "core.sparseCheckout", "true")
        scenarios.append(("sparse", sparse))

        mismatch = self.repository("mismatch")
        elsewhere = self.repository("elsewhere")
        self.initialize(mismatch)
        self.run_git(mismatch, "config", "core.worktree", os.fspath(elsewhere))
        scenarios.append(("root-mismatch", mismatch))

        linked_source = self.repository("linked-source")
        self.initialize(linked_source)
        self.configure_identity(linked_source)
        self.run_git(linked_source, "commit", "--allow-empty", "--quiet", "-m", "empty")
        linked = self.test_root / "linked"
        self.run_git(
            linked_source,
            "worktree",
            "add",
            "--quiet",
            "--detach",
            os.fspath(linked),
            "HEAD",
        )
        scenarios.append(("linked", linked))

        child_source = self.repository("submodule-source")
        self.initialize(child_source)
        self.configure_identity(child_source)
        self.run_git(child_source, "commit", "--allow-empty", "--quiet", "-m", "empty")
        superproject = self.repository("superproject")
        self.initialize(superproject)
        self.configure_identity(superproject)
        self.run_git(superproject, "commit", "--allow-empty", "--quiet", "-m", "empty")
        self.run_git(
            superproject,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--quiet",
            os.fspath(child_source),
            "module",
        )
        scenarios.append(("submodule", superproject / "module"))

        symlinked = self.repository("symlink-admin")
        admin = self.test_root / "outside-admin"
        admin.mkdir()
        (symlinked / ".git").symlink_to(admin, target_is_directory=True)
        scenarios.append(("symlink-admin", symlinked))

        malformed = self.repository("malformed-admin")
        (malformed / ".git").mkdir()
        scenarios.append(("malformed-admin", malformed))

        for name, root in scenarios:
            with self.subTest(topology=name):
                if name != "bare":
                    _write_initializing_root(root)
                identity = (root.stat().st_dev, root.stat().st_ino)
                inspection = WorkspaceRootInspection(
                    WorkspaceRootState.INITIALIZING,
                    identity,
                )
                before = _namespace_snapshot(root)
                with mock.patch.dict(
                    os.environ,
                    self.initialization_environment(),
                    clear=True,
                ):
                    with self.assertRaises(
                        (WorkspaceSecurityError, WorkspaceFormatError)
                    ):
                        initialize_workspace_root(root.resolve(), inspection)
                self.assertEqual(_namespace_snapshot(root), before)

    def test_unexpected_refs_history_index_and_owned_file_drift_are_preserved(
        self,
    ) -> None:
        for case in ("ref", "history", "index", "owned"):
            with self.subTest(case=case):
                root = self.materialize_ready_workspace_fixture(
                    f"unexpected-{case}"
                ).root
                if case == "ref":
                    self.run_git(root, "branch", "extra", "HEAD")
                elif case == "history":
                    self.configure_identity(root)
                    self.run_git(
                        root, "commit", "--allow-empty", "--quiet", "-m", "extra"
                    )
                elif case == "index":
                    (root / "core_documents/global_outline.md").write_bytes(
                        b"index drift"
                    )
                    self.run_git(root, "add", "--", "core_documents/global_outline.md")
                    (root / "core_documents/global_outline.md").write_bytes(b"")
                else:
                    (root / "config/workspace.json").write_bytes(b"owned drift\n")
                before = _namespace_snapshot(root)
                with self.assertRaises(WorkspaceFormatError):
                    Workspace(root)
                self.assertEqual(_namespace_snapshot(root), before)


class WorkspaceV2RecoveryTests(WorkspaceV2GitTestCase):
    """Prove durable ordering, interruption recovery, and terminal instances."""

    def test_journal_is_first_scaffold_write_and_two_verifications_surround_removal(
        self,
    ) -> None:
        root = self.test_root / "ordered"
        events: list[tuple[str, object]] = []
        original_write = initialization_module._write_create_only
        original_verify = initialization_module._verify_ready
        original_unlink = initialization_module.os.unlink

        def record_write(descriptor: int, relative: str, content: bytes) -> None:
            events.append(("write", relative))
            original_write(descriptor, relative, content)

        def record_verify(
            descriptor: int,
            target: Path,
            *,
            journal_present: bool,
            manifest: object | None = None,
        ) -> None:
            events.append(("verify", journal_present))
            original_verify(
                descriptor,
                target,
                journal_present=journal_present,
                manifest=manifest,
            )

        def record_unlink(path: object, *args: object, **kwargs: object) -> None:
            if path == WORKSPACE_INITIALIZATION_JOURNAL_PATH:
                events.append(("remove", path))
            original_unlink(path, *args, **kwargs)

        supported_dir_fd = frozenset(
            record_unlink if operation is original_unlink else operation
            for operation in initialization_module.os.supports_dir_fd
        )

        with (
            mock.patch.dict(os.environ, self.initialization_environment(), clear=True),
            mock.patch.object(
                initialization_module, "_write_create_only", record_write
            ),
            mock.patch.object(initialization_module, "_verify_ready", record_verify),
            mock.patch.object(initialization_module.os, "unlink", record_unlink),
            mock.patch.object(
                initialization_module.os,
                "supports_dir_fd",
                supported_dir_fd,
            ),
        ):
            Workspace(root).initialize()
        writes = [event for event in events if event[0] == "write"]
        self.assertEqual(writes[0], ("write", WORKSPACE_INITIALIZATION_JOURNAL_PATH))
        self.assertEqual(len(writes), 26)
        verify_and_remove = [event for event in events if event[0] != "write"]
        self.assertEqual(
            verify_and_remove,
            [
                ("verify", True),
                ("remove", WORKSPACE_INITIALIZATION_JOURNAL_PATH),
                ("verify", False),
            ],
        )
        self.assert_exact_baseline(root)

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
