"""Candidate preparation and revalidation coverage for story commits."""

from __future__ import annotations

import hashlib
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.git import story_commit_candidates as candidates_module
from story_writing_agents.git.story_commit_candidates import (
    StoryCommitCandidateBinding,
    StoryCommitRevalidation,
    load_story_commit_candidate,
    prepare_story_commit_candidate,
    revalidate_story_commit_candidate,
)
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitPathOperation,
)
from story_writing_agents.workspace.initialization import (
    compose_session_artifact_binding,
    provision_session_resources,
)
from story_writing_agents.workspace.schema import canonical_initializer_scaffold
from story_writing_agents.writing_artifacts import (
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    canonical_artifact_kind,
    project_artifact_change_set,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository

from tests.test_git_session_resources import GitSessionResourceGitTestCase


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def manifest() -> GitBaselineManifest:
    return GitBaselineManifest(
        tuple(
            GitManifestEntry(item.path, sha(item.content))
            for item in canonical_initializer_scaffold()
        )
    )


class StoryCommitGitTestCase(GitSessionResourceGitTestCase):
    """Prepare one isolated applied change on a real session worktree."""

    def setUp(self) -> None:
        super().setUp()
        self.environment = self.session_environment()
        environment_patch = mock.patch.dict(os.environ, self.environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

    def applied_replace(
        self,
        name: str = "Story Space 한글",
        *,
        object_format: str = "sha1",
        after: bytes = "새 장면 🌙\n".encode(),
    ) -> tuple[Path, Path, StoryCommitApplicationEvidence]:
        root = self.initialize_workspace(name, object_format=object_format)
        ready = provision_session_resources(root, "novel")
        session_root = ready.identity.worktree_root
        path = "core_documents/global_outline.md"
        base = {
            item.path: (session_root / item.path).read_bytes()
            for item in canonical_initializer_scaffold()
            if canonical_artifact_kind(item.path) is not None
        }
        operation = ArtifactReplaceOperation(
            path,
            ArtifactKind.CORE_DOCUMENT,
            sha(base[path]),
            after,
            sha(after),
        )
        change_set = build_artifact_change_set(base, (operation,))
        for relative, content in project_artifact_change_set(base, change_set).items():
            target = session_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        # Successful application already crossed the artifact repository lock
        # boundary before it can issue this typed evidence.
        WritingArtifactRepository(session_root).list_paths()
        evidence = StoryCommitApplicationEvidence(
            "novel",
            "application:1",
            "proposal:1",
            "correlation:1",
            change_set,
            change_set.projected_state_digest,
        )
        return root, session_root, evidence

    def prepare_binding(
        self,
        *,
        object_format: str = "sha1",
        after: bytes = "새 장면 🌙\n".encode(),
    ) -> tuple[Path, Path, StoryCommitApplicationEvidence, StoryCommitCandidateBinding]:
        root, session_root, evidence = self.applied_replace(
            object_format=object_format, after=after
        )
        binding = prepare_story_commit_candidate(
            root,
            manifest(),
            evidence,
            "Update opening 🌙",
            ("core outline now exposes the revised opening",),
        )
        return root, session_root, evidence, binding

    @contextmanager
    def replace_session_root(self, session_root: Path):
        moved = session_root.with_name(f"{session_root.name}-generation-a")
        session_root.rename(moved)
        session_root.mkdir()
        try:
            yield moved
        finally:
            session_root.rmdir()
            moved.rename(session_root)

    @contextmanager
    def replace_session_git_indirection(self, session_root: Path):
        """Replace only the linked-worktree .git file while retaining its bytes."""

        git_file = session_root / ".git"
        original = session_root / ".git-generation-original"
        replacement = session_root / ".git-generation-replacement"
        payload = git_file.read_bytes()
        git_file.rename(original)
        replacement.write_bytes(payload)
        replacement.rename(git_file)
        try:
            yield
        finally:
            git_file.unlink()
            original.rename(git_file)


class StoryCommitCandidateContractTests(unittest.TestCase):
    def evidence(
        self, base: dict[str, bytes], operation: object
    ) -> StoryCommitApplicationEvidence:
        change_set = build_artifact_change_set(base, (operation,))  # type: ignore[arg-type]
        return StoryCommitApplicationEvidence(
            "novel",
            "application:1",
            "proposal:1",
            "correlation:1",
            change_set,
            change_set.projected_state_digest,
        )

    def test_exports_and_facade_absence_are_exact(self) -> None:
        self.assertEqual(
            tuple(candidates_module.__all__),
            (
                "StoryCommitCandidateBinding",
                "StoryCommitRevalidation",
                "load_story_commit_candidate",
                "prepare_story_commit_candidate",
                "revalidate_story_commit_candidate",
            ),
        )
        for name in candidates_module.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)

    def test_all_application_operations_map_to_exact_canonical_path_effects(
        self,
    ) -> None:
        old = b"old\n"
        new = "새 값\n".encode()
        cases = (
            (
                {},
                ArtifactCreateOperation(
                    "manuscript/part-01/new.md", ArtifactKind.MANUSCRIPT, new, sha(new)
                ),
                (
                    (
                        "manuscript/part-01/new.md",
                        StoryCommitPathOperation.CREATE,
                        None,
                        sha(new),
                    ),
                ),
            ),
            (
                {"manuscript/part-01/old.md": old},
                ArtifactPatchOperation(
                    "manuscript/part-01/old.md",
                    ArtifactKind.MANUSCRIPT,
                    sha(old),
                    (ArtifactTextHunk(sha(old), 0, 3, b"old", "새 값".encode()),),
                    sha(new),
                ),
                (
                    (
                        "manuscript/part-01/old.md",
                        StoryCommitPathOperation.UPDATE,
                        sha(old),
                        sha(new),
                    ),
                ),
            ),
            (
                {"manuscript/part-01/old.md": old},
                ArtifactReplaceOperation(
                    "manuscript/part-01/old.md",
                    ArtifactKind.MANUSCRIPT,
                    sha(old),
                    new,
                    sha(new),
                ),
                (
                    (
                        "manuscript/part-01/old.md",
                        StoryCommitPathOperation.UPDATE,
                        sha(old),
                        sha(new),
                    ),
                ),
            ),
            (
                {"manuscript/part-01/old.md": old},
                ArtifactDeleteOperation(
                    "manuscript/part-01/old.md", ArtifactKind.MANUSCRIPT, sha(old)
                ),
                (
                    (
                        "manuscript/part-01/old.md",
                        StoryCommitPathOperation.DELETE,
                        sha(old),
                        None,
                    ),
                ),
            ),
            (
                {"manuscript/part-01/z.md": old},
                ArtifactRenameOperation(
                    "manuscript/part-01/z.md",
                    "manuscript/part-01/a.md",
                    ArtifactKind.MANUSCRIPT,
                    sha(old),
                    sha(old),
                ),
                (
                    (
                        "manuscript/part-01/a.md",
                        StoryCommitPathOperation.CREATE,
                        None,
                        sha(old),
                    ),
                    (
                        "manuscript/part-01/z.md",
                        StoryCommitPathOperation.DELETE,
                        sha(old),
                        None,
                    ),
                ),
            ),
        )
        for base, operation, expected in cases:
            with self.subTest(operation=type(operation).__name__):
                paths = candidates_module._candidate_paths(
                    self.evidence(base, operation)
                )  # noqa: SLF001
                self.assertEqual(
                    tuple(
                        (
                            item.path,
                            item.operation,
                            item.before_sha256,
                            item.after_sha256,
                        )
                        for item in paths
                    ),
                    expected,
                )


class StoryCommitCandidateIntegrationTests(StoryCommitGitTestCase):
    def test_apply_identity_rejects_root_replacement_at_every_candidate_read(
        self,
    ) -> None:
        root, session_root, evidence = self.applied_replace(
            "identity-bound"
        )
        observed = os.stat(session_root)
        identity = (observed.st_dev, observed.st_ino)
        artifact_binding = compose_session_artifact_binding(root, "novel")

        def artifact_repository_access(validate_ready):
            return artifact_binding.open_story_commit_artifact_repository(
                validate_ready,
                expected_generation_identity=(
                    artifact_binding._generation_identity
                ),
            )

        with self.replace_session_root(session_root), self.assertRaises(
            StoryCommitError
        ) as before_prepare:
            candidates_module.prepare_identity_bound_story_commit_candidate(
                root,
                manifest(),
                evidence,
                "Update opening",
                ("impact",),
                allowed_worktree_changes=(),
                expected_session_root_identity=identity,
                expected_generation_identity=artifact_binding._generation_identity,
                artifact_repository_access=artifact_repository_access,
            )
        self.assertIs(
            before_prepare.exception.code,
            StoryCommitFailureCode.CANDIDATE_DRIFT,
        )

        original_state = candidates_module._current_artifact_state

        def replace_during_state(repository):
            with self.replace_session_root(session_root):
                return original_state(repository)

        with mock.patch.object(
            candidates_module,
            "_current_artifact_state",
            side_effect=replace_during_state,
        ), self.assertRaises(StoryCommitError) as during_prepare:
            candidates_module.prepare_identity_bound_story_commit_candidate(
                root,
                manifest(),
                evidence,
                "Update opening",
                ("impact",),
                allowed_worktree_changes=(),
                expected_session_root_identity=identity,
                expected_generation_identity=artifact_binding._generation_identity,
                artifact_repository_access=artifact_repository_access,
            )
        self.assertIs(
            during_prepare.exception.code,
            StoryCommitFailureCode.CANDIDATE_DRIFT,
        )

        artifact_binding = compose_session_artifact_binding(root, "novel")

        binding = candidates_module.prepare_identity_bound_story_commit_candidate(
            root,
            manifest(),
            evidence,
            "Update opening",
            ("impact",),
            allowed_worktree_changes=(),
            expected_session_root_identity=identity,
            expected_generation_identity=artifact_binding._generation_identity,
            artifact_repository_access=artifact_repository_access,
        )
        with mock.patch.object(
            candidates_module,
            "_current_artifact_state",
            side_effect=replace_during_state,
        ), self.assertRaises(StoryCommitError) as during_revalidation:
            candidates_module.revalidate_identity_bound_story_commit_candidate(
                binding,
                identity,
                artifact_binding._generation_identity,
                artifact_repository_access=artifact_repository_access,
            )
        self.assertIs(
            during_revalidation.exception.code,
            StoryCommitFailureCode.CANDIDATE_DRIFT,
        )

    def test_prepare_is_git_read_only_durable_and_restart_loadable(self) -> None:
        root, session_root, evidence = self.applied_replace()
        before = self.repository_snapshot(root)
        session_before = (
            self.run_git(session_root, "rev-parse", "HEAD").stdout,
            self.run_git(
                session_root, "status", "--porcelain=v2", "-z", "--untracked-files=all"
            ).stdout,
            (session_root / ".git").read_bytes(),
        )
        binding = prepare_story_commit_candidate(
            root,
            manifest(),
            evidence,
            "Update opening 🌙",
            ("core outline now exposes the revised opening",),
        )
        repeated = prepare_story_commit_candidate(
            root,
            manifest(),
            evidence,
            "Update opening 🌙",
            ("core outline now exposes the revised opening",),
        )
        self.assertEqual(repeated.candidate, binding.candidate)
        self.assertEqual(
            load_story_commit_candidate(
                root, manifest(), "novel", binding.candidate.candidate_id
            ),
            binding.candidate,
        )
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(
            (
                self.run_git(session_root, "rev-parse", "HEAD").stdout,
                self.run_git(
                    session_root,
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--untracked-files=all",
                ).stdout,
                (session_root / ".git").read_bytes(),
            ),
            session_before,
        )
        self.assertNotIn("새 장면", repr(binding))

    def test_revalidation_returns_exact_content_and_rejects_artifact_drift(
        self,
    ) -> None:
        _root, session_root, _evidence, binding = self.prepare_binding()
        plan = revalidate_story_commit_candidate(binding)
        self.assertIsInstance(plan, StoryCommitRevalidation)
        self.assertEqual(
            plan.after_contents,
            (("core_documents/global_outline.md", "새 장면 🌙\n".encode()),),
        )
        (session_root / "core_documents/global_outline.md").write_text(
            "drifted private prose\n", encoding="utf-8"
        )
        with self.assertRaises(StoryCommitError) as caught:
            revalidate_story_commit_candidate(binding)
        self.assertIs(
            caught.exception.code, StoryCommitFailureCode.APPLICATION_MISMATCH
        )
        self.assertNotIn("drifted private prose", repr(caught.exception))

    def test_prestaged_changes_fail_closed_before_candidate_publication(self) -> None:
        root, session_root, evidence = self.applied_replace("prestaged")
        self.run_git(session_root, "add", "--", "core_documents/global_outline.md")
        with self.assertRaises(StoryCommitError) as caught:
            prepare_story_commit_candidate(
                root, manifest(), evidence, "Update opening", ("impact",)
            )
        self.assertIs(caught.exception.code, StoryCommitFailureCode.CANDIDATE_DRIFT)

    def test_noncanonical_untracked_bytes_are_preserved_and_excluded(self) -> None:
        root, session_root, evidence = self.applied_replace("preserve")
        unrelated = session_root / "notes with spaces 한글.txt"
        unrelated.write_bytes(b"private unrelated bytes\n")
        binding = prepare_story_commit_candidate(
            root, manifest(), evidence, "Update opening", ("impact",)
        )
        self.assertEqual(
            tuple(item.path for item in binding.candidate.paths),
            ("core_documents/global_outline.md",),
        )
        self.assertEqual(unrelated.read_bytes(), b"private unrelated bytes\n")

    def test_symlinked_canonical_artifact_fails_closed_without_following_target(
        self,
    ) -> None:
        root, session_root, evidence = self.applied_replace("symlink")
        target = self.test_root / "outside private prose.txt"
        target.write_bytes(b"outside private prose\n")
        artifact = session_root / "core_documents/global_outline.md"
        artifact.unlink()
        artifact.symlink_to(target)
        with self.assertRaises(StoryCommitError) as caught:
            prepare_story_commit_candidate(
                root, manifest(), evidence, "Update opening", ("impact",)
            )
        self.assertIn(
            caught.exception.code,
            {
                StoryCommitFailureCode.CANDIDATE_DRIFT,
                StoryCommitFailureCode.APPLICATION_MISMATCH,
            },
        )
        self.assertEqual(target.read_bytes(), b"outside private prose\n")
        self.assertNotIn("outside private prose", repr(caught.exception))

    def test_unreported_canonical_change_and_content_limit_fail_closed(self) -> None:
        for failure in ("unreported", "oversize"):
            with self.subTest(failure=failure):
                root, session_root, evidence = self.applied_replace(failure)
                patcher = mock.patch.object(candidates_module, "_MAX_CONTENT_BYTES", 1)
                if failure == "unreported":
                    (session_root / "core_documents/narrative_rules.md").write_bytes(
                        b"unreported private rule\n"
                    )
                    patcher = mock.patch.object(
                        candidates_module,
                        "_MAX_CONTENT_BYTES",
                        candidates_module._MAX_CONTENT_BYTES,  # noqa: SLF001
                    )
                with patcher, self.assertRaises(StoryCommitError) as caught:
                    prepare_story_commit_candidate(
                        root, manifest(), evidence, "Update opening", ("impact",)
                    )
                self.assertIs(
                    caught.exception.code,
                    StoryCommitFailureCode.APPLICATION_MISMATCH
                    if failure == "unreported"
                    else StoryCommitFailureCode.INVALID_REQUEST,
                )
                self.assertNotIn("unreported private rule", repr(caught.exception))

    def test_branch_movement_and_missing_candidate_require_reprepare(self) -> None:
        root, _session_root, _evidence, binding = self.prepare_binding()
        with self.assertRaises(StoryCommitError) as missing:
            load_story_commit_candidate(root, manifest(), "novel", "f" * 64)
        self.assertIs(
            missing.exception.code, StoryCommitFailureCode.CANDIDATE_NOT_FOUND
        )
        self.assertNotIn("Story Space", repr(missing.exception))
        self.run_git(
            root,
            "update-ref",
            f"refs/heads/{binding.candidate.branch}",
            binding.internal_tip,
            binding.candidate.branch_tip,
        )
        with self.assertRaises(StoryCommitError) as caught:
            revalidate_story_commit_candidate(binding)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.CANDIDATE_DRIFT)


if __name__ == "__main__":
    unittest.main()
