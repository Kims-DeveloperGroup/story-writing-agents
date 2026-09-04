"""Process-boundary probes for internal-ref checkpoints."""

from __future__ import annotations

import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import session_checkpoints as checkpoints
from story_writing_agents.git.contracts import GitEffectiveIdentity, GitIdentity
from story_writing_agents.git.process import GitCommitTimestamps, GitProcessResult
from story_writing_agents.git.session_checkpoint_contracts import (
    SESSION_CHECKPOINT_MAX_TOTAL_BYTES,
    SESSION_CHECKPOINT_MESSAGE_PREFIX,
    SessionCheckpointError,
    SessionCheckpointFailureCode,
)


class SessionCheckpointProcessTests(unittest.TestCase):
    def test_service_export_and_facade_absence_are_exact(self) -> None:
        self.assertEqual(tuple(checkpoints.__all__), ("SessionCheckpointService",))
        self.assertFalse(hasattr(git_facade, "SessionCheckpointService"))
        self.assertFalse(hasattr(story_writing_agents, "SessionCheckpointService"))

    def test_message_commit_payload_and_salted_intent_are_exact_and_bounded(
        self,
    ) -> None:
        operation = "a" * 32
        message = checkpoints._message("novel", "correlation:1", operation)  # noqa: SLF001
        self.assertEqual(
            message,
            (
                f"{SESSION_CHECKPOINT_MESSAGE_PREFIX}novel\n\n"
                f"Correlation: correlation:1\nOperation: {operation}\n"
            ).encode(),
        )
        identity = GitEffectiveIdentity(
            GitIdentity("Checkpoint Author", "author@example.invalid"),
            GitIdentity("Checkpoint Committer", "committer@example.invalid"),
        )
        payload = checkpoints._commit_payload(  # noqa: SLF001
            "1" * 40,
            "2" * 40,
            identity,
            GitCommitTimestamps(123, 456),
            message,
        )
        self.assertEqual(payload.count(b"parent "), 1)
        self.assertIn(
            b"author Checkpoint Author <author@example.invalid> 123 +0000\n", payload
        )
        self.assertIn(
            b"committer Checkpoint Committer <committer@example.invalid> 456 +0000\n",
            payload,
        )
        self.assertNotEqual(
            checkpoints._intent_digest(operation, payload),  # noqa: SLF001
            checkpoints._intent_digest("b" * 32, payload),  # noqa: SLF001
        )

    def test_complete_existing_tree_enforces_total_byte_bound(self) -> None:
        oid = "1" * 40
        runner = mock.Mock()

        def run(arguments, **kwargs):
            if arguments[0] == "ls-tree":
                return GitProcessResult(
                    f"100644 blob {oid}\tagent_states/orchestrator_state/sessions/novel.json\0".encode(),
                    0,
                )
            if arguments == (
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ):
                self.assertEqual(kwargs["stdin"], f"{oid}\n".encode())
                return GitProcessResult(
                    f"{oid} blob {SESSION_CHECKPOINT_MAX_TOTAL_BYTES + 1}\n".encode(),
                    0,
                )
            raise AssertionError(arguments)

        runner.run.side_effect = run
        with self.assertRaises(SessionCheckpointError) as caught:
            checkpoints._tree_entries(runner, (1, 2), "2" * 40, "novel")  # noqa: SLF001
        self.assertIs(
            caught.exception.code, SessionCheckpointFailureCode.RECOVERY_REQUIRED
        )
        self.assertTrue(
            any(
                call.args[0]
                == (
                    "cat-file",
                    "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                )
                for call in runner.run.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
