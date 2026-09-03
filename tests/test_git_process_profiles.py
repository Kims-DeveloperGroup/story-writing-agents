"""Closed-profile coverage for package-internal Git process execution."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git.contracts import (
    GitEffectiveIdentity,
    GitFailureCode,
    GitIdentity,
)
from story_writing_agents.git import process as process_module
from story_writing_agents.git.process import (
    GitAlternateIndex,
    GitCommitTimestamps,
    GitProcessFailure,
    GitProcessProfile,
    GitProcessRunner,
)

from tests.test_git_adapter_process import _FakeProcess


def _identity() -> GitEffectiveIdentity:
    return GitEffectiveIdentity(
        GitIdentity("Profile Author", "author@example.invalid"),
        GitIdentity("Profile Committer", "committer@example.invalid"),
    )


class GitProcessProfileContractTests(unittest.TestCase):
    def test_exports_profiles_alternate_index_and_facade_absence_are_exact(
        self,
    ) -> None:
        self.assertEqual(
            tuple(process_module.__all__),
            (
                "GitAlternateIndex",
                "GitCommitTimestamps",
                "GitProcessFailure",
                "GitProcessProfile",
                "GitProcessResult",
                "GitProcessRunner",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in GitProcessProfile),
            ("read_only", "mutation", "identity_mutation", "internal_checkpoint"),
        )
        alternate = GitAlternateIndex(Path("/story/.git"), "Novel_01", "a" * 32)
        self.assertTrue(is_dataclass(alternate))
        self.assertTrue(hasattr(alternate, "__slots__"))
        self.assertEqual(
            tuple(field.name for field in fields(alternate)),
            ("common_git", "session_id", "operation_id", "path"),
        )
        timestamps = GitCommitTimestamps(1, 2)
        self.assertEqual(
            tuple(field.name for field in fields(timestamps)),
            ("author_timestamp_seconds", "committer_timestamp_seconds"),
        )
        self.assertEqual(
            alternate.path,
            Path(
                "/story/.git/story-writing-agents/runtime/session-history/"
                "Novel_01/checkpoint-inputs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/index"
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            alternate.session_id = "other"  # type: ignore[misc]
        for name in process_module.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)

    def test_alternate_index_rejects_unbounded_or_aliased_identity(self) -> None:
        invalid = (
            (Path("relative/.git"), "novel", "a" * 32),
            (Path("/story/git"), "novel", "a" * 32),
            (Path("/story/.git"), "bad/id", "a" * 32),
            (Path("/story/.git"), "novel", "A" * 32),
            (Path("/story/.git"), "novel", "٠" * 32),
        )
        for common, session, operation in invalid:
            with self.subTest(common=common, session=session, operation=operation[:2]):
                with self.assertRaises(GitProcessFailure) as caught:
                    GitAlternateIndex(common, session, operation)
                self.assertIs(caught.exception.code, GitFailureCode.INVALID_REQUEST)


class GitProcessProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="git-process-profile-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / ".git").mkdir()
        self.identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        self.runner = GitProcessRunner(self.root)

    def _run(
        self,
        profile: GitProcessProfile,
        *,
        effective_identity: GitEffectiveIdentity | None = None,
        alternate_index: GitAlternateIndex | None = None,
        commit_timestamps: GitCommitTimestamps | None = None,
    ) -> tuple[object, mock.Mock]:
        process = _FakeProcess(stdout=(b"bounded", b""), stderr=(b"",))
        popen = mock.Mock(return_value=process)
        with mock.patch.object(process_module.subprocess, "Popen", popen):
            result = self.runner.run(
                ("status", "--porcelain=v2", "--"),
                expected_identity=self.identity,
                profile=profile,
                effective_identity=effective_identity,
                alternate_index=alternate_index,
                commit_timestamps=commit_timestamps,
            )
        return result, popen

    def test_profiles_build_exact_sanitized_environment(self) -> None:
        hostile = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_DIR": "/escape",
            "GIT_WORK_TREE": "/escape",
            "GIT_CONFIG_GLOBAL": "/hostile",
            "GIT_CONFIG_SYSTEM": "/hostile",
            "GIT_SSH_COMMAND": "private command",
            "GIT_INDEX_FILE": "/foreign/index",
        }
        with mock.patch.dict(os.environ, hostile, clear=True):
            _result, read_popen = self._run(GitProcessProfile.READ_ONLY)
            _result, mutation_popen = self._run(GitProcessProfile.MUTATION)
            _result, identity_popen = self._run(
                GitProcessProfile.IDENTITY_MUTATION,
                effective_identity=_identity(),
            )
        read_environment = read_popen.call_args.kwargs["env"]
        mutation_environment = mutation_popen.call_args.kwargs["env"]
        identity_environment = identity_popen.call_args.kwargs["env"]
        for environment in (
            read_environment,
            mutation_environment,
            identity_environment,
        ):
            for forbidden in (
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_SYSTEM",
                "GIT_SSH_COMMAND",
                "GIT_INDEX_FILE",
            ):
                self.assertNotIn(forbidden, environment)
            self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(environment["GIT_ASKPASS"], os.devnull)
            self.assertEqual(environment["GIT_PAGER"], "cat")
            self.assertEqual(environment["GIT_EDITOR"], "true")
            self.assertEqual(environment["GIT_SEQUENCE_EDITOR"], "true")
            self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(read_environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertNotIn("GIT_OPTIONAL_LOCKS", mutation_environment)
        self.assertEqual(identity_environment["GIT_AUTHOR_NAME"], "Profile Author")
        self.assertEqual(
            identity_environment["GIT_COMMITTER_EMAIL"],
            "committer@example.invalid",
        )
        command = read_popen.call_args.args[0]
        for value in (
            f"core.hooksPath={os.devnull}",
            "commit.gpgSign=false",
            "tag.gpgSign=false",
            "core.fsmonitor=false",
            "diff.external=",
            "pager.status=false",
            "pager.diff=false",
            "pager.log=false",
        ):
            self.assertIn(value, command)

    def test_only_checkpoint_profile_accepts_validated_owned_alternate_index(
        self,
    ) -> None:
        alternate = GitAlternateIndex(self.root / ".git", "novel", "a" * 32)
        alternate.path.parent.mkdir(parents=True)
        _result, popen = self._run(
            GitProcessProfile.INTERNAL_CHECKPOINT,
            effective_identity=_identity(),
            alternate_index=alternate,
            commit_timestamps=GitCommitTimestamps(123, 456),
        )
        self.assertEqual(
            popen.call_args.kwargs["env"]["GIT_INDEX_FILE"],
            os.fspath(alternate.path),
        )
        self.assertEqual(popen.call_args.kwargs["env"]["GIT_AUTHOR_DATE"], "123 +0000")
        self.assertEqual(
            popen.call_args.kwargs["env"]["GIT_COMMITTER_DATE"], "456 +0000"
        )
        invalid = (
            (GitProcessProfile.INTERNAL_CHECKPOINT, None),
            (GitProcessProfile.READ_ONLY, alternate),
            (GitProcessProfile.MUTATION, alternate),
            (GitProcessProfile.IDENTITY_MUTATION, alternate),
        )
        for profile, value in invalid:
            with self.subTest(profile=profile.value):
                with (
                    mock.patch.object(process_module.subprocess, "Popen") as start,
                    self.assertRaises(GitProcessFailure) as caught,
                ):
                    self.runner.run(
                        ("status", "--"),
                        expected_identity=self.identity,
                        profile=profile,
                        effective_identity=_identity()
                        if profile is GitProcessProfile.IDENTITY_MUTATION
                        else None,
                        alternate_index=value,
                    )
                self.assertIs(caught.exception.code, GitFailureCode.INVALID_REQUEST)
                start.assert_not_called()

        foreign = GitAlternateIndex(Path("/foreign/.git"), "novel", "b" * 32)
        with self.assertRaises(GitProcessFailure) as caught:
            self.runner.run(
                ("status", "--"),
                expected_identity=self.identity,
                profile=GitProcessProfile.INTERNAL_CHECKPOINT,
                alternate_index=foreign,
            )
        self.assertIs(caught.exception.code, GitFailureCode.INVALID_REQUEST)

    def test_profile_and_legacy_inputs_cannot_be_combined_or_widened(self) -> None:
        invalid = (
            {"profile": "read_only"},
            {"profile": GitProcessProfile.READ_ONLY, "read_only": True},
            {
                "profile": GitProcessProfile.MUTATION,
                "identity_environment": {"GIT_DIR": "/escape"},
            },
            {
                "profile": GitProcessProfile.IDENTITY_MUTATION,
                "effective_identity": None,
            },
            {
                "profile": GitProcessProfile.MUTATION,
                "effective_identity": _identity(),
            },
        )
        for options in invalid:
            with self.subTest(options=tuple(options)):
                with (
                    mock.patch.object(process_module.subprocess, "Popen") as start,
                    self.assertRaises(GitProcessFailure),
                ):
                    self.runner.run(
                        ("status", "--"),
                        expected_identity=self.identity,
                        **options,  # type: ignore[arg-type]
                    )
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
