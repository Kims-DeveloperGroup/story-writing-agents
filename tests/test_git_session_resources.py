"""Acceptance coverage for exact local Git session resources."""

from __future__ import annotations

import os
import hashlib
import stat
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from story_writing_agents import PLOT_ARCHITECT, Workspace
from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.git.process import GitProcessResult, GitProcessRunner
from story_writing_agents.git.session_contracts import (
    GIT_SESSION_PROVISIONING_DIRECTORY,
    GIT_SESSION_SEED_MESSAGE_PREFIX,
    GitSessionIdentity,
    GitSessionFailureCode,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
)
from story_writing_agents.git import session_resources as resources_module
from story_writing_agents.git import inspection_objects as objects_module
from story_writing_agents.git.session_resources import _Repository
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
    inspect_session_resources,
    list_session_resources,
    provision_session_resources,
    require_ready_session_resources,
)
from story_writing_agents.workspace.session_role_projection import (
    prepare_session_role_projection,
)
from story_writing_agents.workspace import session_role_projection as projection_module
from story_writing_agents.workspace.schema import (
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WorkspaceInitializationJournal,
    canonical_initializer_scaffold,
    encode_initialization_journal,
)

from tests.git_contract_fixture import DisposableGitTestCase, exception_graph
from tests.helpers import materialize_pristine_initialized_workspace




class GitSessionResourceGitTestCase(DisposableGitTestCase):
    """Build isolated schema-v2 repositories in both supported object formats."""

    def session_environment(self) -> dict[str, str]:
        return self.git_environment(
            GIT_AUTHOR_NAME="Session Author",
            GIT_AUTHOR_EMAIL="session-author@example.invalid",
            GIT_COMMITTER_NAME="Session Committer",
            GIT_COMMITTER_EMAIL="session-committer@example.invalid",
        )

    def initialize_workspace(self, name: str, *, object_format: str = "sha1") -> Path:
        root = self.test_root / name
        if object_format == "sha1":
            materialize_pristine_initialized_workspace(root)
            return root
        root.mkdir()
        scaffold = canonical_initializer_scaffold()
        manifest = GitBaselineManifest(
            tuple(
                GitManifestEntry(entry.path, hashlib.sha256(entry.content).hexdigest())
                for entry in scaffold
            )
        )
        for entry in scaffold:
            target = root / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.content)
        (root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).write_bytes(
            encode_initialization_journal(
                WorkspaceInitializationJournal(manifest.manifest_digest)
            )
        )
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
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            Workspace(root).initialize()
        return root

    def repository_snapshot(self, root: Path) -> tuple[bytes, ...]:
        return (
            self.run_git(
                root,
                "for-each-ref",
                "--format=%(refname)%00%(objectname)",
            ).stdout,
            self.run_git(root, "worktree", "list", "--porcelain", "-z").stdout,
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            self.run_git(root, "rev-parse", "HEAD^{tree}").stdout,
            self.run_git(root, "status", "--porcelain=v2", "--").stdout,
            (root / ".git" / "index").read_bytes(),
            self.run_git(root, "count-objects", "-v").stdout,
        )




class GitSessionResourceLifecycleTests(GitSessionResourceGitTestCase):
    """Prove real correlated resources and immutable baseline/seed anchors."""

    def test_create_reopen_and_inventory_are_exact_and_root_invariant(self) -> None:
        root = self.initialize_workspace("Story Space 한글")
        baseline = self.run_git(root, "rev-parse", "HEAD").stdout.decode().strip()
        row = b"refs/heads/main\0" + baseline.encode() + b"\0*\n"
        repository = object.__new__(_Repository)
        query = ("for-each-ref", "--format=%(refname)%00%(objectname)%00%(HEAD)")
        with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(row, 0)) as required:
            self.assertEqual(repository._read_refs().rows, (("refs/heads/main", baseline, True),))
        required.assert_called_once_with(query, read_only=True)
        invalid = (
            row[:-1], row + row, row + row.replace(b"main", b"other"),
            row.replace(b"\0*", b""), row.replace(b"\0*", b"\0?"), row.replace(baseline.encode(), b"bad"),
        )
        for output in invalid:
            with mock.patch.object(
                _Repository, "_required", return_value=GitProcessResult(output, 0)
            ):
                with self.assertRaises(GitSessionResourceError) as caught:
                    repository._read_refs()
            self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        root_before = self.repository_snapshot(root)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            created = provision_session_resources(root, "Novel_01")
        self.assertIs(created.state, GitSessionResourceState.READY)
        self.assertEqual(created.baseline_commit_id, baseline)
        self.assertEqual(created.initial_branch_tip, baseline)
        identity = created.identity
        self.assertTrue(identity.worktree_root.is_dir())
        self.assertEqual(self.run_git(root, "rev-parse", f"refs/heads/{identity.branch}").stdout,
                         (baseline + "\n").encode())
        self.assertEqual(self.run_git(identity.worktree_root, "symbolic-ref", "HEAD").stdout,
                         f"refs/heads/{identity.branch}\n".encode())
        self.assertEqual(self.run_git(root, "status", "--porcelain=v2", "--").stdout, b"")
        self.assertEqual(self.run_git(root, "symbolic-ref", "HEAD").stdout, root_before[2])
        self.assertEqual(self.run_git(root, "rev-parse", "HEAD^{tree}").stdout, root_before[3])
        self.assertEqual((root / ".git" / "index").read_bytes(), root_before[5])

        seed = created.seed_commit_id
        assert seed is not None
        payload = self.run_git(root, "cat-file", "commit", seed).stdout
        header, message = payload.split(b"\n\n", 1)
        self.assertIn(b"author Session Author <session-author@example.invalid>", header)
        self.assertIn(b"committer Session Committer <session-committer@example.invalid>", header)
        self.assertNotIn(b"parent ", header)
        self.assertEqual(message, f"{GIT_SESSION_SEED_MESSAGE_PREFIX}Novel_01\n".encode())
        empty_tree = self.run_git(
            root, "hash-object", "-t", "tree", "--stdin", input_bytes=b""
        ).stdout.strip()
        self.assertIn(b"tree " + empty_tree, header)
        self.assertEqual(self.run_git(root, "rev-parse", identity.internal_ref).stdout,
                         (seed + "\n").encode())

        after_create = self.repository_snapshot(root)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            repeated = provision_session_resources(root, "Novel_01")
            inspected = inspect_session_resources(root, "Novel_01")
            reopened = require_ready_session_resources(
                identity.worktree_root, "Novel_01"
            )
            inventory = list_session_resources(root)
        self.assertEqual(repeated, created)
        self.assertEqual(inspected, created)
        self.assertEqual(reopened, created)
        self.assertEqual(inventory.baseline_commit_id, baseline)
        self.assertEqual(inventory.sessions, (created,))
        self.assertEqual(self.repository_snapshot(root), after_create)
        with mock.patch.object(_Repository, "_root_anchor", side_effect=AssertionError("redundant baseline traversal")), mock.patch.object(
            _Repository, "_root_commit", side_effect=AssertionError("redundant seed traversal")
        ):
            self.assertEqual(inspect_session_resources(root, "Novel_01"), created)
        blobs = (b"", b"binary\n\x00\xff")
        ids = (b"a" * 40, b"b" * 40)
        tree = b"".join(b"100644 blob " + oid + b" " + str(len(blob)).rjust(7).encode() +
                        b"\tfixture/" + str(index).encode() + b"\0"
                        for index, (oid, blob) in enumerate(zip(ids, blobs)))
        frames = b"".join(oid + b" blob " + str(len(blob)).encode() + b"\n" + blob + b"\n"
                          for oid, blob in zip(ids, blobs))
        commits = {"0" * 40: b"baseline\n", "c" * 40: b"seed\n"}
        metadata = b"".join(oid.encode() + b" commit " + str(len(body)).encode() + b"\n" for oid, body in commits.items())
        commit_frames = tuple(oid.encode() + b" commit " + str(len(body)).encode() + b"\n" + body + b"\n" for oid, body in commits.items())
        combined = b"".join(commit_frames) + frames
        commit_input = b"".join(oid.encode() + b"\n" for oid in commits)
        blob_input = b"".join(oid + b"\n" for oid in ids)
        objects = {}
        repository = object.__new__(_Repository)
        with mock.patch.object(_Repository, "_required", side_effect=(
            GitProcessResult(tree, 0), GitProcessResult(combined, 0),
        )) as required:
            self.assertEqual(repository._tree_digests("0" * 40, commit_ids=tuple(commits) + ("0" * 40,), commit_objects=objects), {
                f"fixture/{index}": hashlib.sha256(blob).hexdigest()
                for index, blob in enumerate(blobs)
            })
            self.assertEqual(required.call_args_list, [
                mock.call(("ls-tree", "-r", "-l", "-z", "0" * 40), read_only=True),
                mock.call(("cat-file", "--batch"), stdin=commit_input + blob_input, read_only=True, allow_output_overflow=True),
            ])
            self.assertEqual(objects, commits)
        for bad_metadata, bad_frames in (
            (metadata[:-1], combined),
            (metadata + metadata, combined),
            (metadata.replace(b" commit ", b" blob ", 1), combined),
            (metadata.replace(b"0" * 40, b"d" * 40, 1), combined),
            (metadata.replace(b"commit 9", b"commit 09", 1), combined),
            (b"0" * 40 + b" missing\n" + metadata.split(b"\n", 1)[1], combined),
            (metadata, combined.replace(b" commit ", b" blob ", 1)),
            (metadata, combined.replace(b"0" * 40, b"d" * 40, 1)),
            (metadata, combined.replace(b"commit 9", b"commit 8", 1)),
            (metadata, combined[:-1]),
            (metadata, combined + b"unexpected residue"),
        ):
            with mock.patch.object(_Repository, "_required", side_effect=(
                GitProcessResult(tree, 0), GitProcessResult(bad_frames if bad_metadata == metadata else bad_metadata, 0),
            )), self.assertRaises(GitSessionResourceError) as caught:
                repository._tree_digests("0" * 40, commit_ids=tuple(commits), commit_objects={})
            self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        oversized_tip = b"x" * (resources_module._MAX_ADMIN_FILE_BYTES + 1)
        for missing_frames in (
            commit_frames[0] + b"c" * 40 + b" missing\n" + frames,
            commit_frames[0] + commit_frames[1].replace(b" commit ", b" blob ") + frames,
            commit_frames[0] + b"c" * 40 + b" commit " + str(len(oversized_tip)).encode() + b"\n" + oversized_tip + b"\n" + frames,
        ):
            objects = {}
            with mock.patch.object(_Repository, "_required", side_effect=(
                GitProcessResult(tree, 0), GitProcessResult(missing_frames, 0),
            )):
                self.assertEqual(repository._tree_digests("0" * 40, commit_ids=tuple(commits), commit_objects=objects), {
                    f"fixture/{index}": hashlib.sha256(blob).hexdigest() for index, blob in enumerate(blobs)
                })
            self.assertEqual(objects, {"0" * 40: commits["0" * 40]})
        facts = resources_module._Facts(
            {identity.branch: baseline}, {identity.internal_ref: created.seed_commit_id},
            {identity.worktree_root: (baseline, f"refs/heads/{identity.branch}")}, {},
        )
        advanced = resources_module._Facts(
            {identity.branch: "d" * 40}, {identity.internal_ref: "e" * 40},
            {identity.worktree_root: ("d" * 40, f"refs/heads/{identity.branch}")}, {},
        )
        with mock.patch.object(_Repository, "_root_anchor", return_value=True) as anchor, mock.patch.object(
            _Repository, "_root_commit", return_value=created.seed_commit_id
        ) as traverse, mock.patch.object(_Repository, "_required", return_value=GitProcessResult(payload, 0)), mock.patch.object(
            _Repository, "_verify_worktree_indirection", return_value=True
        ), mock.patch.object(_Repository, "_session_index_is_clean", return_value=True):
            inspected = repository._classify(identity, baseline, advanced, inspected_baseline=baseline, inspected_tip=("e" * 40, b"parent historical tip"))
            self.assertIs(inspected.state, GitSessionResourceState.READY)
            anchor.assert_called_once_with("d" * 40, baseline)
            traverse.assert_called_once_with("e" * 40)
        for unsuitable in (None, b"malformed optional TIP"):
            with mock.patch.object(_Repository, "_root_commit", return_value=None) as traverse:
                inspected = repository._classify(identity, baseline, facts, inspected_baseline=baseline, inspected_tip=(created.seed_commit_id, unsuitable))
            self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
            traverse.assert_called_once_with(created.seed_commit_id)
        with mock.patch.object(_Repository, "_root_anchor", return_value=True), mock.patch.object(
            _Repository, "_required", return_value=GitProcessResult(b"", 0)
        ) as required:
            missing = repository._classify(identity, baseline, facts, inspected_seed=(created.seed_commit_id, None))
            self.assertIs(missing.state, GitSessionResourceState.RECOVERY_REQUIRED)
            required.assert_called_once_with(("cat-file", "commit", created.seed_commit_id), read_only=True)
        expected = {f"fixture/{index}": hashlib.sha256(blob).hexdigest() for index, blob in enumerate(blobs)}
        manifest = GitBaselineManifest(tuple(GitManifestEntry(path, value) for path, value in expected.items()))
        refs = resources_module._RefSnapshot((("refs/heads/main", baseline, True),))
        prefetched = {baseline: b"discard prior optimistic bytes", created.seed_commit_id: b"discard seed"}
        formatted = b"\0" + resources_module.GIT_BASELINE_COMMIT_MESSAGE.encode() + b"\n"
        for fallback in (resources_module._InspectionBatchOverflow(), resources_module._InspectionPrefetchUnsuitable()):
            prefetched.update({baseline: b"discard prior optimistic bytes", created.seed_commit_id: b"discard seed"})
            with mock.patch.object(_Repository, "_inspection_tree_digests", side_effect=fallback) as optimistic, mock.patch.object(
                _Repository, "_tree_digests", return_value=expected
            ) as trees, mock.patch.object(
                _Repository, "_required", side_effect=(GitProcessResult(formatted, 0), GitProcessResult(b"", 0))
            ) as required:
                self.assertEqual(repository._verify_main(manifest, refs, commit_objects=prefetched, internal_tip=created.seed_commit_id), baseline)
                self.assertEqual(prefetched, {})
                self.assertEqual(optimistic.call_count + trees.call_count, 2)
                self.assertEqual(optimistic.call_args.args[1:3], (baseline, created.seed_commit_id))
                self.assertIs(optimistic.call_args.args[3], prefetched)
                self.assertEqual(trees.call_args_list[0], mock.call(baseline))
                self.assertEqual(required.call_args_list, [
                    mock.call(("show", "-s", "--format=%P%x00%B", baseline), read_only=True),
                    mock.call(("status", "--porcelain=v1", "-z", "--untracked-files=no"), read_only=True),
                ])
        malformed = GitSessionResourceError(GitSessionFailureCode.RECOVERY_REQUIRED, operation=GitSessionOperationKind.INSPECT,
            state=GitSessionResourceState.RECOVERY_REQUIRED, recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY)
        with mock.patch.object(_Repository, "_inspection_tree_digests", side_effect=malformed) as trees, mock.patch.object(
            _Repository, "_tree_digests"
        ) as legacy, self.assertRaises(GitSessionResourceError):
            repository._verify_main(manifest, refs, commit_objects={})
        self.assertEqual(trees.call_count, 1)
        legacy.assert_not_called()
        repository.runner = mock.Mock()
        repository.identity = (1, 2)
        for code in (resources_module.GitFailureCode.PROCESS_OUTPUT_EXCEEDED, resources_module.GitFailureCode.PROCESS_START_FAILED, resources_module.GitFailureCode.PROCESS_TIMED_OUT):
            repository.runner.run.side_effect = resources_module.GitProcessFailure(code)
            exception = resources_module._InspectionBatchOverflow if code is resources_module.GitFailureCode.PROCESS_OUTPUT_EXCEEDED else GitSessionResourceError
            with self.assertRaises(exception) as caught:
                repository._required(("cat-file", "--batch"), read_only=True, allow_output_overflow=True, allow_unsuitable_result=True)
            if code is resources_module.GitFailureCode.PROCESS_START_FAILED:
                with mock.patch.object(_Repository, "_inspection_tree_digests", side_effect=caught.exception) as trees, mock.patch.object(
                    _Repository, "_tree_digests"
                ) as legacy, self.assertRaises(GitSessionResourceError):
                    repository._verify_main(manifest, refs, commit_objects={})
                self.assertEqual(trees.call_count, 1)
                legacy.assert_not_called()
        repository.runner.run.side_effect = None
        repository.runner.run.return_value = GitProcessResult(b"unsupported batch expression", 1)
        with self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
            repository._required(("cat-file", "--batch"), read_only=True, allow_unsuitable_result=True)
        with self.assertRaises(GitSessionResourceError):
            repository._required(("cat-file", "--batch"), read_only=True)
        with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"", 0)) as required:
            self.assertEqual(repository._tree_digests("0" * 40), {})
            required.assert_called_once_with(("ls-tree", "-r", "-l", "-z", "0" * 40), read_only=True)

        def raw_object_id(kind, body, algorithm):
            return hashlib.new(algorithm, kind + b" " + str(len(body)).encode() + b"\0" + body).hexdigest()

        def raw_frame(oid, kind, body):
            return oid.encode() + b" " + kind + b" " + str(len(body)).encode() + b"\n" + body + b"\n"

        for algorithm in ("sha1", "sha256"):
            blob_ids = tuple(raw_object_id(b"blob", blob, algorithm) for blob in blobs)
            leaves = tuple(b"100644 " + str(index).encode() + b"\0" + bytes.fromhex(oid) for index, oid in enumerate(blob_ids))
            child_tree = b"".join(leaves)
            child_id = raw_object_id(b"tree", child_tree, algorithm)
            raw_root = b"40000 fixture\0" + bytes.fromhex(child_id)
            root_id = raw_object_id(b"tree", raw_root, algorithm)
            author_headers = b"author Fixture <fixture@example.invalid> 0 +0000\ncommitter Fixture <fixture@example.invalid> 0 +0000\n\n"
            root_commit = b"tree " + root_id.encode() + b"\n" + author_headers + resources_module.GIT_BASELINE_COMMIT_MESSAGE.encode() + b"\n"
            raw_baseline = raw_object_id(b"commit", root_commit, algorithm)
            empty_tree_id = raw_object_id(b"tree", b"", algorithm)
            tip_commit = b"tree " + empty_tree_id.encode() + b"\n" + author_headers + b"seed fixture\n"
            raw_tip = raw_object_id(b"commit", tip_commit, algorithm)
            raw_objects = [(raw_baseline, b"commit", root_commit), (raw_tip, b"commit", tip_commit),
                           (root_id, b"tree", raw_root), (child_id, b"tree", child_tree),
                           *[(oid, b"blob", blob) for oid, blob in zip(blob_ids, blobs)]]
            raw_frames = [raw_frame(*item) for item in raw_objects]
            queries = [raw_baseline.encode(), raw_tip.encode(), raw_baseline.encode() + b":",
                       *[raw_baseline.encode() + b":" + path.encode() for path in ("fixture", "fixture/0", "fixture/1")]]
            raw_stdin = b"".join(query + b"\n" for query in queries)
            objects = {}
            with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"".join(raw_frames), 0)) as required:
                self.assertEqual(repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, objects), expected)
                required.assert_called_once_with(("cat-file", "--batch", "--buffer"), stdin=raw_stdin, read_only=True,
                                                allow_output_overflow=True, allow_unsuitable_result=True)
                self.assertEqual(objects, {raw_baseline: root_commit, raw_tip: tip_commit})
            objects = {}
            with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(raw_frames[0] + b"".join(raw_frames[2:]), 0)) as required:
                self.assertEqual(repository._inspection_tree_digests(manifest, raw_baseline, raw_baseline, objects), expected)
                self.assertEqual(required.call_args.kwargs["stdin"], queries[0] + b"\n" + b"".join(query + b"\n" for query in queries[2:]))
                self.assertEqual(objects, {raw_baseline: root_commit})
            for optional in (queries[1] + b" missing\n", raw_frame(raw_tip, b"blob", b"not a commit"),
                             raw_frame(raw_tip, b"commit", b"x" * (resources_module._MAX_ADMIN_FILE_BYTES + 1))):
                altered = raw_frames.copy()
                altered[1] = optional
                objects = {}
                with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"".join(altered), 0)):
                    self.assertEqual(repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, objects), expected)
                    self.assertEqual(objects, {raw_baseline: root_commit})
            wrong_id = "d" * len(raw_baseline)
            for index, body in (
                (0, root_commit.replace(root_id.encode(), wrong_id.encode(), 1)),
                (2, raw_root.replace(bytes.fromhex(child_id), bytes.fromhex(wrong_id))),
                (2, raw_root.replace(b"fixture\0", b"unexpected\0")),
                (2, raw_root.replace(b"40000", b"100644")),
                (2, b"40000 empty\0" + bytes.fromhex(empty_tree_id) + raw_root),
                (3, child_tree + b"100644 extra\0" + bytes.fromhex(blob_ids[0])),
                (3, leaves[0] + leaves[0] + leaves[1]), (3, leaves[1] + leaves[0]),
                (3, child_tree.replace(b"100644", b"0100644", 1)),
                (3, child_tree.replace(b"100644", b"100755", 1)),
                (3, child_tree.replace(b"0\0", b"nested/0\0", 1)),
                (3, child_tree.replace(b"0\0", b".\0", 1)), (3, leaves[0][:-1]),
            ):
                altered = raw_frames.copy()
                oid, kind, _body = raw_objects[index]
                altered[index] = raw_frame(oid, kind, body)
                with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"".join(altered), 0)), self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                    repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, {})
            for index in (2, 3, 4):
                altered = raw_frames.copy()
                _oid, kind, body = raw_objects[index]
                altered[index] = raw_frame(wrong_id, kind, body)
                with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"".join(altered), 0)), self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                    repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, {})
            for index in (2, 3, 4, 5):
                for required_frame in (queries[index] + b" missing\n", raw_frame(raw_objects[index][0], b"tag", raw_objects[index][2])):
                    altered = raw_frames.copy()
                    altered[index] = required_frame
                    with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"".join(altered), 0)), self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                        repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, {})
            missing_tree = raw_frames.copy()
            missing_tree[2] = queries[2] + b" missing\n"
            for malformed_batch in (
                queries[0] + b" missing\n" + b"".join(raw_frames[1:]),
                raw_frame(wrong_id, b"commit", root_commit) + b"".join(raw_frames[1:]),
                raw_frame(raw_baseline, b"blob", root_commit) + b"".join(raw_frames[1:]),
                b"".join(raw_frames)[:-1], b"".join(raw_frames) + b"residue",
                b"".join(missing_tree) + b"malformed after unsuitable",
                b"".join(raw_frames[:-1]) + raw_frames[-1].replace(b" blob ", b" blob 0", 1),
            ):
                with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(malformed_batch, 0)), self.assertRaises(GitSessionResourceError) as caught:
                    repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, {})
                self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
            for bound, value in (("_MAX_INSPECTION_OBJECTS", len(queries) - 1), ("_MAX_STDOUT_BYTES", len(raw_stdin) - 1)):
                with mock.patch.object(resources_module, bound, value), mock.patch.object(_Repository, "_required") as required, self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                    repository._inspection_tree_digests(manifest, raw_baseline, raw_tip, {})
                required.assert_not_called()
            unsupported_manifest = GitBaselineManifest((GitManifestEntry("fixture", expected["fixture/0"]), GitManifestEntry("fixture/0", expected["fixture/0"])))
            with mock.patch.object(_Repository, "_required") as required, self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                repository._inspection_tree_digests(unsupported_manifest, raw_baseline, raw_tip, {})
            required.assert_not_called()
            with mock.patch.object(resources_module, "_MAX_INSPECTION_OBJECTS", 1), self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                resources_module._inspection_tree_children(memoryview(child_tree), len(raw_baseline) // 2)
            ref_root = self.test_root / f"loose-refs-{algorithm}"
            common = ref_root / ".git"
            heads = common / "refs/heads"
            heads.mkdir(parents=True)
            object_file = common / "objects" / raw_baseline[:2] / raw_baseline[2:]
            object_file.parent.mkdir(parents=True)
            object_file.write_bytes(b"")
            config = b"[core]\n\trepositoryformatversion = " + (b"0" if algorithm == "sha1" else b"1") + b"\n\tbare = false\n"
            if algorithm == "sha256":
                config += b"[extensions]\n\tobjectformat = sha256\n"
            head = b"ref: refs/heads/main\n"
            (common / "config").write_bytes(config)
            (common / "HEAD").write_bytes(head)
            main_ref = heads / "main"
            main_ref.write_bytes(raw_baseline.encode() + b"\n")
            output = b"refs/heads/main\0" + raw_baseline.encode() + b"\0*\n"
            descriptor = os.open(common, os.O_RDONLY | os.O_DIRECTORY)
            root_stat = ref_root.stat()
            loose = _Repository(ref_root, common, descriptor, (root_stat.st_dev, root_stat.st_ino))
            try:
                self.assertEqual(resources_module._loose_ref_width(config), len(raw_baseline))
                for content in (b"", b"malformed object contents"):
                    object_file.write_bytes(content)
                    self.assertEqual(loose._loose_ref_output(), self.run_git(ref_root, *query).stdout)
                with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(output, 0)) as required:
                    legacy = loose._read_refs()
                    required.assert_called_once_with(query, read_only=True)
                    required.reset_mock()
                    self.assertEqual(loose._read_refs(allow_loose=True), legacy)
                    loose._require_refs_current(legacy, allow_loose=True)
                    required.assert_not_called()
                with mock.patch.object(_Repository, "_loose_ref_output", return_value=b"malformed\n"), mock.patch.object(_Repository, "_required") as required:
                    with self.assertRaises(GitSessionResourceError) as caught:
                        loose._read_refs(allow_loose=True)
                    self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
                    required.assert_not_called()
                for actor in ("list", "provision", "inspect"):
                    with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(output, 0)) as required, mock.patch.object(
                        _Repository, "_verify_main", return_value=raw_baseline
                    ) as verify, mock.patch.object(_Repository, "_facts", return_value=resources_module._Facts({}, {}, {}, {})), mock.patch.object(
                        _Repository, "_classify", return_value=created
                    ), mock.patch.object(_Repository, "_require_exhaustive") as exhaustive, mock.patch.object(
                        _Repository, "_loose_ref_output", wraps=loose._loose_ref_output
                    ) as fast:
                        actual = loose.list(manifest) if actor == "list" else getattr(loose, actor)(manifest, created.identity)
                        self.assertEqual(actual, resources_module.GitSessionResourceInventory(raw_baseline, ()) if actor == "list" else created)
                        self.assertEqual(fast.call_count, 2 if actor == "inspect" else 0)
                        self.assertEqual(exhaustive.call_count, 1 if actor == "list" else 0)
                        self.assertEqual(verify.call_args.kwargs.get("allow_loose", False), actor == "inspect")
                        self.assertEqual(required.call_args_list, [] if actor == "inspect" else [mock.call(query, read_only=True)] * 2)
                optional = heads / "optional"
                self.assertEqual(loose._loose_ref_output(), output)
                for invalid in (b"", b"malformed\n", b"ref: refs/heads/main\n", b"0" * len(raw_tip) + b"\n", raw_tip.upper().encode() + b"\n"):
                    optional.write_bytes(invalid)
                    self.assertIsNone(loose._loose_ref_output())
                optional.write_bytes(raw_tip.encode() + b"\n")
                self.assertIsNone(loose._loose_ref_output())
                tip_object = common / "objects" / raw_tip[:2] / raw_tip[2:]
                tip_object.parent.mkdir(exist_ok=True)
                complete_output = output + b"refs/heads/optional\0" + raw_tip.encode() + b"\0 \n"
                for content in (b"", b"malformed object contents"):
                    tip_object.write_bytes(content)
                    self.assertEqual(loose._loose_ref_output(), complete_output)
                    self.assertEqual(loose._loose_ref_output(), self.run_git(ref_root, *query).stdout)
                tip_object.unlink()
                for fallback_path in (common / "objects/pack/fixture.pack", common / "objects/info/alternates"):
                    fallback_path.parent.mkdir(exist_ok=True)
                    fallback_path.write_bytes(b"fixture\n")
                    with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(complete_output, 0)) as required:
                        self.assertEqual(loose._read_refs(allow_loose=True).rows, legacy.rows + (("refs/heads/optional", raw_tip, False),))
                        required.assert_called_once_with(query, read_only=True)
                    fallback_path.unlink()
                optional.unlink()
                for payload in (b"", b"# pack-refs with: peeled fully-peeled sorted\n" + raw_baseline.encode() + b" refs/heads/main\n"):
                    (common / "packed-refs").write_bytes(payload)
                    main_ref.unlink()
                    self.assertIsNone(loose._loose_ref_output())
                    (common / "packed-refs").unlink()
                    main_ref.write_bytes(raw_baseline.encode() + b"\n")
                for path, payload in ((common / "HEAD", raw_baseline.encode() + b"\n"), (common / "HEAD", b"ref: refs/heads/missing\n"),
                                      (common / "config", config + b"[include]\n\tpath = other\n"), (common / "config", config + b"[core]\n\tbare = false\n")):
                    original_bytes = path.read_bytes()
                    path.write_bytes(payload)
                    self.assertIsNone(loose._loose_ref_output())
                    path.write_bytes(original_bytes)
                actual_scan, actual_open, actual_close = os.scandir, os.open, os.close
                mutation = None
                changed = False
                close_failure = False
                class RefIterator:
                    def __init__(self, iterator):
                        self.iterator = iterator
                    def __iter__(self):
                        return iter(self.iterator)
                    def close(self):
                        nonlocal changed
                        self.iterator.close()
                        if not changed:
                            changed = True
                            if mutation is not None:
                                mutation()
                            if close_failure:
                                raise OSError("iterator cleanup failed")
                for path, payload in ((common / "config", config + b"\n"), (common / "HEAD", head + b"\n"),
                                      (main_ref, raw_tip.encode() + b"\n"), (common / "refs/shadow", raw_baseline.encode() + b"\n")):
                    original_bytes = path.read_bytes() if path.exists() else None
                    mutation = lambda: path.write_bytes(payload)
                    changed = False
                    with mock.patch.object(resources_module.os, "scandir", side_effect=lambda fd: RefIterator(actual_scan(fd))):
                        self.assertIsNone(loose._loose_ref_output())
                    path.unlink() if original_bytes is None else path.write_bytes(original_bytes)
                mutation = None
                opened = []
                def tracked_open(*args, **kwargs):
                    fd = actual_open(*args, **kwargs)
                    opened.append(fd)
                    return fd
                for bound, value in (("_MAX_REFS", 1), ("_MAX_STDOUT_BYTES", 1), ("_MAX_ADMIN_FILE_BYTES", 1),
                                     ("_MAX_LOOSE_REF_DESCRIPTORS", 4), ("_LOOSE_REF_TIMEOUT_SECONDS", 0)):
                    opened.clear()
                    with mock.patch.object(resources_module, bound, value), mock.patch.object(resources_module.os, "open", side_effect=tracked_open):
                        self.assertIsNone(loose._loose_ref_output())
                    if bound == "_MAX_LOOSE_REF_DESCRIPTORS":
                        self.assertLessEqual(len(opened), value - 1)
                    for fd in opened:
                        with self.assertRaises(OSError):
                            os.fstat(fd)
                for cleanup_kind in ("iterator", "descriptor"):
                    changed = False
                    close_failure = cleanup_kind == "iterator"
                    opened.clear()
                    failed_close = False
                    def cleanup(fd):
                        nonlocal failed_close
                        actual_close(fd)
                        if cleanup_kind == "descriptor" and not failed_close:
                            failed_close = True
                            raise OSError("descriptor cleanup failed")
                    with mock.patch.object(resources_module.os, "open", side_effect=tracked_open), mock.patch.object(
                        resources_module.os, "scandir", side_effect=lambda fd: RefIterator(actual_scan(fd))
                    ), mock.patch.object(resources_module.os, "close", side_effect=cleanup), mock.patch.object(_Repository, "_required") as required:
                        with self.assertRaises(GitSessionResourceError) as caught:
                            loose._read_refs(allow_loose=True)
                        self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
                        required.assert_not_called()
                    for fd in opened:
                        with self.assertRaises(OSError):
                            os.fstat(fd)
                close_failure = False
                self.assertEqual(loose._loose_ref_output(), output)
                def store_object(kind, body):
                    oid = raw_object_id(kind, body, algorithm)
                    path = common / "objects" / oid[:2] / oid[2:]
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(zlib.compress(kind + b" " + str(len(body)).encode() + b"\0" + body))
                    return oid
                for oid, kind, body in raw_objects:
                    self.assertEqual(store_object(kind, body), oid)
                batch = b"".join(query + b"\n" for query in queries)
                frames = self.run_git(ref_root, "cat-file", "--batch", "--buffer", input_bytes=batch).stdout
                self.assertEqual(frames, b"".join(raw_frames))
                self.assertEqual(loose._loose_object_output(batch), frames)
                with mock.patch.object(_Repository, "_required") as required:
                    self.assertEqual(loose._inspection_tree_digests(manifest, raw_baseline, raw_tip, {}, allow_loose=True), expected)
                    required.assert_not_called()
                def fallback(allow=True):
                    with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(frames, 0)) as required:
                        self.assertEqual(loose._inspection_tree_digests(manifest, raw_baseline, raw_tip, {}, allow_loose=allow), expected)
                        required.assert_called_once_with(("cat-file", "--batch", "--buffer"), read_only=True, stdin=batch, allow_output_overflow=True, allow_unsuitable_result=True)
                with mock.patch.object(_Repository, "_loose_object_output", side_effect=AssertionError("loose outside inspect")):
                    fallback(False)
                selected = common / "objects" / blob_ids[0][:2] / blob_ids[0][2:]
                original_object = selected.read_bytes()
                for payload in (b"", b"not zlib", original_object[:-1], original_object + b"trailing", original_object + zlib.compress(b"blob 0\0"), zlib.compress(b"blob 01\0x"),
                                zlib.compress(b"blob 1\0wrong size"), zlib.compress(b"unknown 0\0"), zlib.compress(b"blob 1\0x")):
                    selected.write_bytes(payload)
                    self.assertIsNone(loose._loose_object_output(batch))
                    fallback()
                selected.write_bytes(original_object)
                for storage in ("missing", "symlink", "hardlink"):
                    selected.unlink()
                    if storage == "symlink": selected.symlink_to(object_file)
                    if storage == "hardlink": os.link(object_file, selected)
                    self.assertIsNone(loose._loose_object_output(batch))
                    fallback()
                    if selected.is_symlink() or selected.exists(): selected.unlink()
                    selected.write_bytes(original_object)
                for path in (common / "packed-refs", common / "shallow", common / "refs/replace", common / "info/grafts",
                             common / "objects/info/alternates", common / "objects/pack/fixture.pack"):
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(b"unsupported\n")
                    self.assertIsNone(loose._loose_object_output(batch))
                    fallback()
                    path.unlink()
                for mode in (b"100755", b"120000", b"160000"):
                    tree = store_object(b"tree", child_tree.replace(b"100644", mode, 1))
                    commit = store_object(b"commit", root_commit.replace(root_id.encode(), tree.encode(), 1))
                    self.assertIsNone(loose._loose_object_output(commit.encode() + b":0\n"))
                empty = store_object(b"tree", b"")
                self.assertEqual(loose._loose_object_output(empty.encode() + b"\n"), self.run_git(ref_root, "cat-file", "--batch", input_bytes=empty.encode() + b"\n").stdout)
                unused = store_object(b"tree", b"40000 empty\0" + bytes.fromhex(empty) + raw_root)
                commit = store_object(b"commit", root_commit.replace(root_id.encode(), unused.encode(), 1))
                with mock.patch.object(_Repository, "_required") as required, self.assertRaises(resources_module._InspectionPrefetchUnsuitable):
                    loose._inspection_tree_digests(manifest, commit, raw_tip, {}, allow_loose=True)
                required.assert_not_called()
                retained = {}
                with mock.patch.object(_Repository, "_tree_digests", return_value=expected) as legacy, mock.patch.object(_Repository, "_required", side_effect=(GitProcessResult(formatted, 0), GitProcessResult(b"", 0))):
                    self.assertEqual(loose._verify_main(manifest, resources_module._RefSnapshot((("refs/heads/main", commit, True),)), commit_objects=retained, internal_tip=raw_tip, allow_loose=True), commit)
                    self.assertEqual(retained, {})
                    legacy.assert_called_once_with(commit)
                selected.write_bytes(zlib.compress(b"blob 8192\0" + b"x" * 8192))
                with mock.patch.object(objects_module, "MAX_BATCH_BYTES", 2048):
                    self.assertIsNone(loose._loose_object_output(batch))
                    fallback()
                selected.write_bytes(original_object)
                with mock.patch.object(objects_module, "MAX_BATCH_BYTES", 2048):
                    self.assertIsNone(loose._loose_object_output((raw_baseline.encode() + b"\n") * 16))
                for limit, value in (("MAX_DESCRIPTORS", 4), ("MAX_NODES", 1), ("MAX_BATCH_BYTES", 1), ("MAX_METADATA_BYTES", 1), ("DEADLINE_SECONDS", 0)):
                    opened.clear()
                    with mock.patch.object(objects_module, limit, value), mock.patch.object(objects_module.os, "open", side_effect=tracked_open): self.assertIsNone(loose._loose_object_output(batch))
                    if limit == "MAX_DESCRIPTORS": self.assertLessEqual(len(opened), value - 1)
                    for fd in opened:
                        with self.assertRaises(OSError): os.fstat(fd)
                    os.fstat(descriptor)
                original_read = os.read
                for target in (common / "config", selected, selected.parent, common / "objects", common / "refs", common / "packed-refs", common / "refs/replace", common / "shallow", common / "info/grafts"):
                    moved, changed = target.with_name(target.name + ".held"), False
                    watched = selected.stat().st_ino
                    present = target.exists()
                    saved = target.read_bytes() if target.is_file() else None
                    def race(fd, size):
                        nonlocal changed
                        value = original_read(fd, size)
                        if not value and os.fstat(fd).st_ino == watched and not changed:
                            changed = True
                            if not present: target.write_bytes(b"fresh override\n")
                            elif saved is not None: target.write_bytes(saved + b"changed")
                            else: target.rename(moved); target.mkdir()
                        return value
                    try:
                        with mock.patch.object(objects_module.os, "read", side_effect=race): self.assertIsNone(loose._loose_object_output(batch))
                        self.assertTrue(changed)
                        if target == common / "refs": self.assertEqual(loose._loose_object_output(batch), frames)
                        else: fallback()
                    finally:
                        if not present: target.unlink()
                        elif saved is not None: target.write_bytes(saved)
                        else: target.rmdir(); moved.rename(target)
                mutation, changed = None, False
                for cleanup_kind in ("iterator", "descriptor", "root", "common"):
                    opened.clear()
                    failed_close, close_failure = False, cleanup_kind == "iterator"
                    target = ref_root if cleanup_kind == "root" else common
                    moved = target.with_name(target.name + ".held")
                    def object_cleanup(fd):
                        nonlocal failed_close
                        actual_close(fd)
                        if not failed_close and cleanup_kind != "iterator":
                            failed_close = True
                            if cleanup_kind == "descriptor": raise OSError("object cleanup failed")
                            target.rename(moved); target.mkdir()
                    try:
                        with mock.patch.object(objects_module.os, "open", side_effect=tracked_open), mock.patch.object(objects_module.os, "scandir", side_effect=lambda fd: RefIterator(actual_scan(fd))), mock.patch.object(objects_module.os, "close", side_effect=object_cleanup), mock.patch.object(_Repository, "_required") as required:
                            with self.assertRaises(GitSessionResourceError) as caught: loose._inspection_tree_digests(manifest, raw_baseline, raw_tip, {}, allow_loose=True)
                            self.assertIs(caught.exception.code, GitSessionFailureCode.UNSAFE_ROOT if cleanup_kind in ("root", "common") else GitSessionFailureCode.RECOVERY_REQUIRED)
                            required.assert_not_called()
                        for fd in opened:
                            with self.assertRaises(OSError): os.fstat(fd)
                        os.fstat(descriptor)
                    finally:
                        if cleanup_kind in ("root", "common") and moved.exists(): target.rmdir(); moved.rename(target)
                close_failure = False
                for malformed in (b"", b"\n", b"invalid\n"):
                    with self.assertRaises(GitSessionResourceError) as caught: loose._loose_object_output(malformed)
                    self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
                self.assertEqual(loose._loose_object_output(batch), frames)
            finally:
                os.close(descriptor)
        partial = GitSessionIdentity(root, "Blob_Only")
        blob = self.run_git(root, "hash-object", "-w", "--stdin", input_bytes=b"invalid seed object\n").stdout.decode().strip()
        self.run_git(root, "update-ref", partial.internal_ref, blob)
        partial_before = self.repository_snapshot(root)
        self.assertFalse(partial.worktree_root.exists())
        self.assertNotIn(f"refs/heads/{partial.branch}".encode(), partial_before[0])
        self.assertIs(inspect_session_resources(root, partial.session_id).state, GitSessionResourceState.RECOVERY_REQUIRED)
        with self.assertRaises(GitSessionResourceError) as caught:
            require_ready_session_resources(root, partial.session_id)
        self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(caught.exception.operation, GitSessionOperationKind.REQUIRE_READY)
        self.assertIs(caught.exception.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertEqual(self.repository_snapshot(root), partial_before)
        (self.test_root / "sha256-probe").mkdir()
        if self.run_git(self.test_root / "sha256-probe", "init", "--quiet", "--initial-branch=main", "--template=", "--object-format=sha256", check=False).returncode == 0:
            sha_root = self.initialize_workspace("SHA-256 Story", object_format="sha256")
            with mock.patch.dict(os.environ, self.session_environment(), clear=True):
                self.assertIs(provision_session_resources(sha_root, "Novel_02").state, GitSessionResourceState.READY)
                self.assertIs(require_ready_session_resources(sha_root, "Novel_02").state, GitSessionResourceState.READY)








        role = prepare_session_role_projection(
            PLOT_ARCHITECT, b'---\nname: "plot_architect"\nexact role bytes\n'
        )
        role_root = self.initialize_workspace("role-unrelated-tracked")
        role_ready = provision_session_resources(role_root, "novel")
        role_session = role_ready.identity.worktree_root
        self.configure_identity(role_root)
        unrelated = (
            ".agents-backup/private.bin",
            "agents/private.bin",
            "nested/.agents/private.bin",
        )
        self.commit_fixture_files(
            role_session,
            {path: b"unrelated tracked bytes\n" for path in unrelated},
            "Track unrelated names",
        )
        role_binding = compose_session_artifact_binding(role_root, "novel")
        with role_binding.open_ready_resources() as access:
            access.install_provider_role(role)
        role_path = role_session / ".agents/agents" / role.native_agent_id / "agent.md"
        self.assertEqual(role_path.read_bytes(), role.content)
        self.assertEqual(self.run_git(role_session, "check-ignore", "-q", "--", str(role_path), check=False).returncode, 0)
        for path in unrelated:
            self.assertEqual((role_session / path).read_bytes(), b"unrelated tracked bytes\n")

        for source, initial in (
            ("exclude", b"prefix\n/.agents/\n!/.agents/\n"),
            ("gitignore", b"!/.agents/\n"),
        ):
            excluded_root = self.initialize_workspace(f"role-{source}-negation")
            excluded = provision_session_resources(excluded_root, "novel")
            session = excluded.identity.worktree_root
            if source == "exclude":
                exclusion = excluded_root / ".git/info/exclude"
                exclusion.parent.mkdir(parents=True, exist_ok=True)
                exclusion.write_bytes(initial)
            else:
                self.configure_identity(excluded_root)
                self.commit_fixture_files(session, {".gitignore": initial}, "Unignore roles")
            binding = compose_session_artifact_binding(excluded_root, "novel")
            if source == "exclude":
                with binding.open_ready_resources() as access:
                    access.install_provider_role(role)
                self.assertEqual(exclusion.read_bytes(), initial + b"/.agents/\n")
            else:
                with binding.open_ready_resources() as access, self.assertRaises(
                    SessionArtifactBindingError
                ) as caught:
                    access.install_provider_role(role)
                self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
                self.assertFalse((session / ".agents").exists())
                self.assertEqual(
                    self.run_git(session, "check-ignore", "-q", "--no-index", "--", ".agents/", check=False).returncode,
                    1,
                )



class GitSessionResourceRestartTests(GitSessionResourceGitTestCase):
    """Prove durable evidence controls every interrupted provisioning replay."""





    def test_internal_ref_prefix_resumes_branch_and_worktree_in_order(self) -> None:
        root = self.initialize_workspace("internal-prefix")
        real_cas = _Repository._cas_ref

        def stop_before_branch(repository: _Repository, ref: str, target: str) -> None:
            if ref.startswith("refs/heads/"):
                raise OSError
            real_cas(repository, ref, target)

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_cas_ref", new=stop_before_branch),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertIsNotNone(interrupted.seed_commit_id)
        self.assertIsNone(interrupted.current_branch_tip)
        self.assertIsNotNone(interrupted.current_internal_tip)
        self.assertFalse(interrupted.identity.worktree_root.exists())

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
            inspected = inspect_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertEqual(inspected, recovered)
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)
        baseline = recovered.baseline_commit_id
        advanced = self.run_git(
            root, "commit-tree", f"{baseline}^{{tree}}", "-p", baseline,
            input_bytes=b"Concurrent main advance\n", environment=self.session_environment(),
        ).stdout.decode().strip()
        for hook, ref, target in (
            ("_verify_main", "refs/heads/main", advanced),
            ("_classify", recovered.identity.internal_ref, baseline),
        ):
            previous = self.run_git(root, "rev-parse", ref).stdout.decode().strip()
            original = getattr(_Repository, hook)

            def drift(repository, *args, **kwargs):
                self.run_git(root, "update-ref", ref, target, previous)
                return original(repository, *args, **kwargs)

            with mock.patch.dict(os.environ, self.session_environment(), clear=True):
                with mock.patch.object(_Repository, hook, drift):
                    with self.assertRaises(GitSessionResourceError) as caught:
                        inspect_session_resources(root, "novel")
            self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
            self.assertIs(caught.exception.operation, GitSessionOperationKind.INSPECT)
            self.assertIs(caught.exception.state, GitSessionResourceState.RECOVERY_REQUIRED)
            self.assertIs(caught.exception.recovery_action, GitSessionRecoveryAction.OPERATOR_RECOVERY)
            self.assertEqual(self.run_git(root, "rev-parse", ref).stdout.decode().strip(), target)
            self.run_git(root, "update-ref", ref, previous, target)






class GitSessionResourceSafetyTests(GitSessionResourceGitTestCase):
    """Prove repository serialization and no-follow collision behavior."""



    def test_preexisting_file_and_symlink_siblings_are_preserved_before_mutation(
        self,
    ) -> None:
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                root = self.initialize_workspace(f"sibling-{kind}")
                identity = GitSessionIdentity(root, "novel")
                identity.worktree_root.parent.mkdir()
                outside = self.test_root / f"outside-{kind}"
                outside.write_bytes(b"private target bytes\n")
                if kind == "file":
                    identity.worktree_root.write_bytes(b"private sibling bytes\n")
                else:
                    identity.worktree_root.symlink_to(outside)
                before = self.repository_snapshot(root)
                with mock.patch.dict(
                    os.environ, self.session_environment(), clear=True
                ):
                    result = provision_session_resources(root, "novel")
                self.assertIs(result.state, GitSessionResourceState.RECOVERY_REQUIRED)
                self.assertEqual(self.repository_snapshot(root), before)
                self.assertEqual(outside.read_bytes(), b"private target bytes\n")
                if kind == "file":
                    self.assertEqual(identity.worktree_root.read_bytes(), b"private sibling bytes\n")
                else:
                    self.assertTrue(identity.worktree_root.is_symlink())













        role = prepare_session_role_projection(
            PLOT_ARCHITECT, b'---\nname: "plot_architect"\nexact role bytes\n'
        )
        real_install = projection_module._install_immutable_regular
        for timing in ("before", "after"):
            root = self.initialize_workspace(f"role-index-stage-{timing}")
            ready = provision_session_resources(root, "novel")
            session = ready.identity.worktree_root
            binding = compose_session_artifact_binding(root, "novel")
            selected = session / ".agents/agents" / role.native_agent_id / "agent.md"
            staged = False

            def stage_at_publication(*args, **kwargs):
                nonlocal staged
                if timing == "before":
                    selected.write_bytes(role.content)
                    selected.chmod(0o400)
                else:
                    real_install(*args, **kwargs)
                self.run_git(session, "add", "-f", "--", selected.relative_to(session).as_posix())
                staged = True
                if timing == "before":
                    real_install(*args, **kwargs)

            with self.assertRaises(SessionArtifactBindingError) as caught:
                with binding.open_ready_resources() as access, mock.patch.object(
                    projection_module, "_install_immutable_regular", new=stage_at_publication
                ):
                    access.install_provider_role(role)
            self.assertTrue(staged)
            self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
            self.assertEqual(selected.read_bytes(), role.content)
            self.assertIn(selected.relative_to(session).as_posix().encode() + b"\0",
                          self.run_git(session, "ls-files", "-z").stdout)

        real_write = os.write
        root = self.initialize_workspace("role-exclude-short-write")
        ready = provision_session_resources(root, "novel")
        session = ready.identity.worktree_root
        exclude = root / ".git/info/exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_bytes(b"preexisting\n")
        exclude_inode = exclude.stat().st_ino
        writes: list[int] = []

        def write_prefix_then_rest(descriptor: int, content: bytes) -> int:
            if os.fstat(descriptor).st_ino != exclude_inode:
                return real_write(descriptor, content)
            self.assertFalse((session / ".agents").exists())
            count = real_write(descriptor, content[:3] if not writes else content)
            writes.append(count)
            return count

        binding = compose_session_artifact_binding(root, "novel")
        with binding.open_ready_resources() as access, mock.patch.object(
            projection_module.os, "write", side_effect=write_prefix_then_rest
        ):
            access.install_provider_role(role)
        self.assertEqual(writes, [3, len(b"/.agents/\n") - 3])
        self.assertEqual(exclude.read_bytes(), b"preexisting\n/.agents/\n")
        self.assertEqual((session / ".agents/agents" / role.native_agent_id / "agent.md").read_bytes(), role.content)

        for failure in ("zero", "oversized", "bool", "partial-error"):
            failed_root = self.initialize_workspace(f"role-exclude-write-{failure}")
            failed_ready = provision_session_resources(failed_root, "novel")
            failed_session = failed_ready.identity.worktree_root
            failed_exclude = failed_root / ".git/info/exclude"
            failed_exclude.parent.mkdir(parents=True, exist_ok=True)
            failed_exclude.write_bytes(b"preexisting\n")
            failed_inode = failed_exclude.stat().st_ino
            attempts = 0

            def fail_append(descriptor: int, content: bytes) -> int:
                nonlocal attempts
                if os.fstat(descriptor).st_ino != failed_inode:
                    return real_write(descriptor, content)
                attempts += 1
                if failure == "partial-error":
                    if attempts == 1:
                        return real_write(descriptor, content[:3])
                    raise OSError("PRIVATE_SHORT_WRITE_FAILURE")
                if failure == "zero":
                    return 0
                return len(content) + 1 if failure == "oversized" else True

            failed_binding = compose_session_artifact_binding(failed_root, "novel")
            with self.assertRaises(SessionArtifactBindingError) as caught:
                with failed_binding.open_ready_resources() as access, mock.patch.object(
                    projection_module.os, "write", side_effect=fail_append
                ):
                    access.install_provider_role(role)
            self.assertEqual(attempts, 2 if failure == "partial-error" else 1)
            self.assertEqual(failed_exclude.read_bytes(),
                             b"preexisting\n" + (b"/.a" if failure == "partial-error" else b""))
            self.assertFalse((failed_session / ".agents").exists())
            self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
            self.assertNotIn("PRIVATE", repr(caught.exception))

if __name__ == "__main__":
    unittest.main()
