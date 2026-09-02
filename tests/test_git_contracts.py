"""Pure acceptance coverage for the provider-independent Git contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitBaselineInspection,
    GitBaselineManifest,
    GitChangeKind,
    GitCommitMetadata,
    GitDiffEntry,
    GitDiffMetadata,
    GitEffectiveIdentity,
    GitFailureCode,
    GitIdentity,
    GitLogMetadata,
    GitManifestEntry,
    GitOperationError,
    GitOperationKind,
    GitRecoveryAction,
    GitRepositoryAdapter,
    GitRepositoryState,
    GitRepositoryTopology,
    GitStatus,
    GitStatusEntry,
    GitTopologyKind,
)
from story_writing_agents.git import adapter as adapter_module
from story_writing_agents.git import contracts as contracts_module
from story_writing_agents.git import process as process_module

from tests.git_contract_fixture import digest, exception_graph


_FACADE_EXPORTS = (
    "GIT_BASELINE_BRANCH",
    "GIT_BASELINE_COMMIT_MESSAGE",
    "GitOperationKind",
    "GitRepositoryState",
    "GitTopologyKind",
    "GitChangeKind",
    "GitFailureCode",
    "GitRecoveryAction",
    "GitOperationError",
    "GitManifestEntry",
    "GitBaselineManifest",
    "GitIdentity",
    "GitEffectiveIdentity",
    "GitRepositoryTopology",
    "GitBaselineInspection",
    "GitStatusEntry",
    "GitStatus",
    "GitDiffEntry",
    "GitDiffMetadata",
    "GitCommitMetadata",
    "GitLogMetadata",
    "GitRepositoryAdapter",
)

_CONTRACT_TYPES = (
    GitManifestEntry,
    GitBaselineManifest,
    GitIdentity,
    GitEffectiveIdentity,
    GitRepositoryTopology,
    GitBaselineInspection,
    GitStatusEntry,
    GitStatus,
    GitDiffEntry,
    GitDiffMetadata,
    GitCommitMetadata,
    GitLogMetadata,
)

_ZERO = "0" * 40
_ONE = "1" * 40
_DIGEST = "a" * 64


def _identity() -> GitIdentity:
    return GitIdentity("Private Author", "private@example.invalid")


def _commit() -> GitCommitMetadata:
    identity = _identity()
    return GitCommitMetadata(
        _ZERO,
        _ONE,
        (),
        identity,
        identity,
        "1700000000 +0000",
        "1700000000 +0000",
        _DIGEST,
        26,
    )


class GitContractTests(unittest.TestCase):
    """Prove closed values, strict validation, privacy, and import boundaries."""

    def test_facade_and_direct_module_exports_are_exact_and_direct_only(self) -> None:
        self.assertEqual(tuple(git_facade.__all__), _FACADE_EXPORTS)
        self.assertEqual(tuple(contracts_module.__all__), _FACADE_EXPORTS[:-1])
        self.assertEqual(tuple(adapter_module.__all__), ("GitRepositoryAdapter",))
        for name in _FACADE_EXPORTS:
            self.assertIs(getattr(git_facade, name), globals()[name])
            self.assertNotIn(name, story_writing_agents.__all__)
        self.assertFalse(
            any(
                name.startswith(("encode_", "decode_", "run_", "execute_"))
                for name in git_facade.__all__
            )
        )

    def test_package_has_exactly_three_approved_internal_edges(self) -> None:
        source_root = Path(git_facade.__file__).resolve().parent
        modules = {
            "git": source_root / "__init__.py",
            "git.adapter": source_root / "adapter.py",
            "git.contracts": source_root / "contracts.py",
        }
        edges: set[tuple[str, str]] = set()
        for source, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != 1:
                    continue
                target = f"git.{node.module}" if node.module else "git"
                if target in modules:
                    edges.add((source, target))
        self.assertEqual(
            edges,
            {
                ("git", "git.adapter"),
                ("git", "git.contracts"),
                ("git.adapter", "git.contracts"),
            },
        )

    def test_closed_enum_values_are_exact(self) -> None:
        expectations = {
            GitOperationKind: (
                "inspect_topology",
                "inspect_baseline",
                "create_baseline",
                "status",
                "diff_metadata",
                "log_metadata",
            ),
            GitRepositoryState: (
                "absent",
                "initialized_without_baseline",
                "baseline_committed",
                "recovery_required",
            ),
            GitTopologyKind: (
                "absent",
                "standalone",
                "parent_repository",
                "linked_worktree",
                "submodule",
                "sparse_checkout",
                "root_mismatch",
                "symlinked_administrative_path",
                "malformed_administrative_path",
            ),
            GitChangeKind: (
                "added",
                "modified",
                "deleted",
                "renamed",
                "copied",
                "type_changed",
                "unmerged",
                "untracked",
            ),
            GitFailureCode: (
                "invalid_request",
                "invalid_manifest",
                "unsafe_root",
                "unsupported_topology",
                "executable_unavailable",
                "identity_invalid",
                "process_start_failed",
                "process_timed_out",
                "process_output_exceeded",
                "command_failed",
                "output_invalid",
                "manifest_drift",
                "index_mismatch",
                "baseline_mismatch",
                "cancelled",
                "recovery_required",
                "io_failed",
            ),
            GitRecoveryAction: (
                "none",
                "retry",
                "inspect_repository",
                "recreate_workspace",
            ),
        }
        for enum_type, values in expectations.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)

    def test_adapter_surface_has_only_six_typed_operations(self) -> None:
        public = {
            name
            for name, value in inspect.getmembers(GitRepositoryAdapter)
            if not name.startswith("_") and callable(value)
        }
        self.assertEqual(
            public,
            {
                "inspect_topology",
                "inspect_baseline",
                "create_baseline",
                "status",
                "diff_metadata",
                "log_metadata",
            },
        )
        self.assertEqual(
            str(inspect.signature(GitRepositoryAdapter)),
            "(root: 'str | os.PathLike[str]') -> 'None'",
        )
        self.assertEqual(
            str(inspect.signature(GitRepositoryAdapter.inspect_topology)),
            "(self) -> 'GitRepositoryTopology'",
        )
        self.assertEqual(
            str(inspect.signature(GitRepositoryAdapter.create_baseline)),
            "(self, manifest: 'GitBaselineManifest') -> 'GitBaselineInspection'",
        )

    def test_import_and_construction_have_no_filesystem_or_process_effect(self) -> None:
        root = Path("/definitely/not/inspected/by/construction")
        with (
            mock.patch.object(os, "lstat") as lstat,
            mock.patch.object(process_module.subprocess, "Popen") as popen,
        ):
            adapter = GitRepositoryAdapter(root)
        self.assertEqual(adapter._root, root)  # noqa: SLF001
        lstat.assert_not_called()
        popen.assert_not_called()

    def test_contract_dataclasses_are_frozen_slotted_and_exactly_typed(self) -> None:
        for contract in _CONTRACT_TYPES:
            with self.subTest(contract=contract.__name__):
                self.assertTrue(is_dataclass(contract))
                self.assertTrue(hasattr(contract, "__slots__"))
        entry = GitManifestEntry("core/outline.md", _DIGEST)
        with self.assertRaises(FrozenInstanceError):
            entry.path = "other.md"  # type: ignore[misc]
        self.assertEqual(
            tuple(field.name for field in fields(entry)), ("path", "content_digest")
        )

    def test_manifest_is_nonempty_sorted_unique_and_digest_is_canonical(self) -> None:
        entries = (
            GitManifestEntry("core/characters.md", digest(b"characters")),
            GitManifestEntry("manuscript/part-01/chapter-01.md", digest(b"chapter")),
        )
        manifest = GitBaselineManifest(entries)
        payload = json.dumps(
            [
                {"content_digest": entry.content_digest, "path": entry.path}
                for entry in entries
            ],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        self.assertEqual(manifest.manifest_digest, hashlib.sha256(payload).hexdigest())
        for invalid in (
            (),
            list(entries),
            tuple(reversed(entries)),
            (entries[0], entries[0]),
            (object(),),
        ):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(GitOperationError) as caught:
                    GitBaselineManifest(invalid)  # type: ignore[arg-type]
                self.assertEqual(caught.exception.code, GitFailureCode.INVALID_MANIFEST)

    def test_manifest_paths_and_digests_reject_options_refs_and_pathspecs(self) -> None:
        hostile = (
            "",
            "/absolute.md",
            "../escape.md",
            "core/../escape.md",
            "core//outline.md",
            "core/.git/config",
            "core/.GIT/config",
            "core/outline md",
            "core/*.md",
            "core/$USER.md",
            "core\\outline.md",
            "core/outline.md/",
            "core/\ud800.md",
            f"core/{'a' * 1024}.md",
        )
        for path in hostile:
            with self.subTest(path=repr(path)):
                with self.assertRaises(GitOperationError) as caught:
                    GitManifestEntry(path, _DIGEST)
                self.assertEqual(caught.exception.code, GitFailureCode.INVALID_MANIFEST)
        self.assertEqual(GitManifestEntry("--literal.md", _DIGEST).path, "--literal.md")
        for invalid in ("A" * 64, "a" * 63, "g" * 64, b"a" * 64, None):
            with self.subTest(digest=repr(invalid)):
                with self.assertRaises(GitOperationError):
                    GitManifestEntry("core/outline.md", invalid)  # type: ignore[arg-type]

    def test_identity_is_strict_and_never_rendered(self) -> None:
        secret_name = "Sensitive Author"
        secret_email = "sensitive@example.invalid"
        identity = GitIdentity(secret_name, secret_email)
        effective = GitEffectiveIdentity(identity, identity)
        self.assertNotIn(secret_name, repr(identity))
        self.assertNotIn(secret_email, repr(identity))
        self.assertNotIn(secret_name, repr(effective))
        self.assertNotIn(secret_email, repr(effective))
        for name, email in (
            ("", secret_email),
            ("name<bad", secret_email),
            ("name\nsecret", secret_email),
            (secret_name, "has space@example.invalid"),
            (secret_name, "bad@example.invalid\rsecret"),
            ("\ud800", secret_email),
        ):
            with self.subTest(name=repr(name), email=repr(email)):
                with self.assertRaises(GitOperationError) as caught:
                    GitIdentity(name, email)
                self.assertEqual(caught.exception.code, GitFailureCode.IDENTITY_INVALID)

    def test_projection_contracts_enforce_bounds_and_coherent_state(self) -> None:
        topology = GitRepositoryTopology(GitTopologyKind.STANDALONE)
        effective = GitEffectiveIdentity(_identity(), _identity())
        committed = GitBaselineInspection(
            topology,
            GitRepositoryState.BASELINE_COMMITTED,
            _DIGEST,
            _ZERO,
            _ONE,
            effective,
        )
        self.assertEqual(committed.commit_id, _ZERO)
        self.assertNotIn("Private Author", repr(committed))
        for values in (
            (
                topology,
                GitRepositoryState.BASELINE_COMMITTED,
                _DIGEST,
                None,
                None,
                None,
            ),
            (topology, GitRepositoryState.ABSENT, _DIGEST, _ZERO, _ONE, effective),
        ):
            with self.subTest(state=values[1]):
                with self.assertRaises(GitOperationError):
                    GitBaselineInspection(*values)

        status_entry = GitStatusEntry("core/outline.md", None, GitChangeKind.MODIFIED)
        self.assertTrue(GitStatus("main", _ZERO, ()).clean)
        self.assertFalse(GitStatus("main", _ZERO, (status_entry,)).clean)
        with self.assertRaises(GitOperationError):
            GitStatusEntry("core/outline.md", None, None)
        with self.assertRaises(GitOperationError):
            GitStatus("main\nsecret", _ZERO, ())

        diff = GitDiffEntry(
            "core/outline.md",
            None,
            GitChangeKind.MODIFIED,
            False,
            _ZERO,
            _ONE,
        )
        self.assertEqual(GitDiffMetadata(_ZERO, (diff,)).entries, (diff,))
        with self.assertRaises(GitOperationError):
            GitDiffEntry(
                "core/outline.md", None, GitChangeKind.MODIFIED, 1, _ZERO, _ONE
            )  # type: ignore[arg-type]

        commit = _commit()
        log = GitLogMetadata((commit,))
        self.assertEqual(log.commits, (commit,))
        self.assertNotIn("Private Author", repr(commit))
        self.assertNotIn("private@example.invalid", repr(commit))
        with self.assertRaises(GitOperationError):
            GitLogMetadata(tuple(commit for _ in range(129)))

    def test_errors_have_fixed_diagnostics_recovery_and_no_caller_content(self) -> None:
        secret = "caller-content-MUST-NOT-LEAK"
        expectations = {
            GitFailureCode.INVALID_REQUEST: GitRecoveryAction.NONE,
            GitFailureCode.INVALID_MANIFEST: GitRecoveryAction.NONE,
            GitFailureCode.UNSAFE_ROOT: GitRecoveryAction.RECREATE_WORKSPACE,
            GitFailureCode.EXECUTABLE_UNAVAILABLE: GitRecoveryAction.RETRY,
            GitFailureCode.PROCESS_TIMED_OUT: GitRecoveryAction.INSPECT_REPOSITORY,
        }
        try:
            raise ValueError(secret)
        except ValueError:
            error = GitOperationError(
                GitFailureCode.COMMAND_FAILED,
                operation=GitOperationKind.STATUS,
                exit_code=7,
            )
            error.__cause__ = None
            error.__context__ = None
            error.__suppress_context__ = True
        self.assertEqual(str(error), "Git command failed.")
        self.assertEqual(error.diagnostic, "Git command failed.")
        self.assertEqual(error.exit_code, 7)
        self.assertEqual(exception_graph(error), (error,))
        self.assertNotIn(secret, repr(error))
        for code, action in expectations.items():
            with self.subTest(code=code.value):
                observed = GitOperationError(code)
                self.assertIs(observed.recovery_action, action)
                self.assertNotIn(secret, str(observed))

    def test_constants_are_exact_and_no_compatibility_codec_or_migration_exists(
        self,
    ) -> None:
        self.assertEqual(GIT_BASELINE_BRANCH, "main")
        self.assertEqual(GIT_BASELINE_COMMIT_MESSAGE, "Initialize story workspace")
        namespace = set(vars(git_facade)) | set(vars(contracts_module))
        forbidden_fragments = ("legacy", "migrate", "codec", "serialize", "deserialize")
        self.assertFalse(
            any(
                fragment in name.casefold()
                for name in namespace
                for fragment in forbidden_fragments
            )
        )


if __name__ == "__main__":
    unittest.main()
