"""Pure contract coverage for internal-ref session checkpoints."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import session_checkpoint_contracts as contracts
from story_writing_agents.git.session_checkpoint_contracts import (
    SessionCheckpointError,
    SessionCheckpointFailureCode,
    SessionCheckpointOwner,
    SessionCheckpointPathOperation,
    SessionCheckpointRecoveryAction,
    SessionCheckpointRequest,
    SessionCheckpointResult,
    SessionCheckpointSnapshot,
    SessionCheckpointState,
)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_snapshot(
    path: str = "agent_states/orchestrator_state/sessions/novel.json",
    content: bytes = b'{"session":"novel"}\n',
) -> SessionCheckpointSnapshot:
    return SessionCheckpointSnapshot(
        SessionCheckpointOwner.WORKSPACE,
        path,
        SessionCheckpointPathOperation.CREATE,
        None,
        digest(content),
        content,
    )


class SessionCheckpointContractTests(unittest.TestCase):
    def test_exports_enums_shapes_and_facade_absence_are_exact(self) -> None:
        self.assertEqual(
            tuple(contracts.__all__),
            (
                "SESSION_CHECKPOINT_MAX_PATHS",
                "SESSION_CHECKPOINT_MAX_TOTAL_BYTES",
                "SESSION_CHECKPOINT_MESSAGE_PREFIX",
                "SessionCheckpointOwner",
                "SessionCheckpointPathFamily",
                "SessionCheckpointPathOperation",
                "SessionCheckpointState",
                "SessionCheckpointFailureCode",
                "SessionCheckpointRecoveryAction",
                "SessionCheckpointError",
                "SessionCheckpointPathClassification",
                "SessionCheckpointSnapshot",
                "SessionCheckpointRequest",
                "SessionCheckpointResult",
                "classify_session_checkpoint_path",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in SessionCheckpointOwner),
            (
                "workspace",
                "cli_context",
                "backend",
                "conversation_catalog",
                "collaboration_history",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in SessionCheckpointPathOperation),
            ("create", "update", "delete"),
        )
        self.assertEqual(
            tuple(item.value for item in SessionCheckpointState),
            (
                "unchanged",
                "running",
                "committed",
                "outcome_unknown",
                "recovery_required",
            ),
        )
        value = create_snapshot()
        self.assertTrue(is_dataclass(value))
        self.assertTrue(hasattr(value, "__slots__"))
        self.assertEqual(
            tuple(field.name for field in fields(value)),
            ("owner", "path", "operation", "before_sha256", "after_sha256", "content"),
        )
        with self.assertRaises(FrozenInstanceError):
            value.path = "other"  # type: ignore[misc]
        for name in contracts.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)

    def test_create_update_delete_and_empty_request_invariants_are_exact(self) -> None:
        before = b"before\n"
        after = b"after\n"
        path = "agent_states/orchestrator_state/sessions/novel.json"
        values = (
            create_snapshot(path, after),
            SessionCheckpointSnapshot(
                SessionCheckpointOwner.WORKSPACE,
                path,
                SessionCheckpointPathOperation.UPDATE,
                digest(before),
                digest(after),
                after,
            ),
            SessionCheckpointSnapshot(
                SessionCheckpointOwner.WORKSPACE,
                path,
                SessionCheckpointPathOperation.DELETE,
                digest(before),
                None,
                None,
            ),
        )
        for value in values:
            with self.subTest(operation=value.operation):
                if value.content is not None:
                    self.assertNotIn(value.content.decode(), repr(value))
        empty = SessionCheckpointRequest("novel", "correlation:empty", ())
        self.assertEqual(empty.snapshots, ())
        invalid = (
            (
                SessionCheckpointPathOperation.CREATE,
                digest(before),
                digest(after),
                after,
            ),
            (
                SessionCheckpointPathOperation.UPDATE,
                digest(before),
                digest(before),
                after,
            ),
            (SessionCheckpointPathOperation.DELETE, digest(before), None, after),
        )
        for operation, before_digest, after_digest, content in invalid:
            with self.subTest(operation=operation):
                with self.assertRaises(SessionCheckpointError) as caught:
                    SessionCheckpointSnapshot(
                        SessionCheckpointOwner.WORKSPACE,
                        path,
                        operation,
                        before_digest,
                        after_digest,
                        content,
                    )
                self.assertIs(
                    caught.exception.code, SessionCheckpointFailureCode.SNAPSHOT_INVALID
                )

    def test_request_requires_unique_utf8_order_same_session_owner_and_bounds(
        self,
    ) -> None:
        first = create_snapshot()
        second_content = b"defaults\n"
        second = SessionCheckpointSnapshot(
            SessionCheckpointOwner.CLI_CONTEXT,
            "agent_states/orchestrator_state/conversation_defaults/novel.json",
            SessionCheckpointPathOperation.CREATE,
            None,
            digest(second_content),
            second_content,
        )
        ordered = tuple(
            sorted((first, second), key=lambda item: item.path.encode("utf-8"))
        )
        self.assertEqual(
            SessionCheckpointRequest("novel", "correlation:1", ordered).snapshots,
            ordered,
        )
        for snapshots, session in (
            (tuple(reversed(ordered)), "novel"),
            ((first, first), "novel"),
            ((first,), "other"),
        ):
            with self.subTest(
                session=session, paths=tuple(item.path for item in snapshots)
            ):
                with self.assertRaises(SessionCheckpointError):
                    SessionCheckpointRequest(session, "correlation:1", snapshots)

    def test_result_state_matrix_and_content_free_failures_are_closed(self) -> None:
        tip = "1" * 40
        cases = (
            (
                SessionCheckpointState.UNCHANGED,
                SessionCheckpointRecoveryAction.NONE,
                None,
                None,
            ),
            (
                SessionCheckpointState.RUNNING,
                SessionCheckpointRecoveryAction.INSPECT,
                "a" * 32,
                None,
            ),
            (
                SessionCheckpointState.OUTCOME_UNKNOWN,
                SessionCheckpointRecoveryAction.INSPECT,
                "a" * 32,
                None,
            ),
            (
                SessionCheckpointState.RECOVERY_REQUIRED,
                SessionCheckpointRecoveryAction.OPERATOR_RECOVERY,
                "a" * 32,
                None,
            ),
            (
                SessionCheckpointState.COMMITTED,
                SessionCheckpointRecoveryAction.NONE,
                "a" * 32,
                "2" * 40,
            ),
        )
        for state, action, operation, checkpoint in cases:
            result = SessionCheckpointResult(
                "novel",
                "correlation:1",
                state,
                action,
                checkpoint or tip,
                operation,
                checkpoint,
            )
            self.assertIs(result.state, state)
        error = SessionCheckpointError(
            SessionCheckpointFailureCode.RECOVERY_REQUIRED,
            SessionCheckpointRecoveryAction.OPERATOR_RECOVERY,
        )
        self.assertEqual(str(error), "Checkpoint requires recovery.")
        self.assertNotIn("private runtime bytes", repr(error))


if __name__ == "__main__":
    unittest.main()
