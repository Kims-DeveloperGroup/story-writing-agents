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








if __name__ == "__main__":
    unittest.main()
