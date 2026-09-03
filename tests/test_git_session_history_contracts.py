"""Pure contract coverage for durable Git session-history records."""

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import session_history_contracts as contracts_module

from story_writing_agents.git.session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GIT_SESSION_HISTORY_MAX_PATHS,
    GIT_SESSION_HISTORY_MAX_RECORD_BYTES,
    GIT_SESSION_HISTORY_MAX_TEXT_BYTES,
    GIT_SESSION_HISTORY_SCHEMA_VERSION,
    GitSessionCandidatePath,
    GitSessionCandidatePathOperation,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryAction,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryInspection,
    GitSessionHistoryRecoveryAction,
    GitSessionHistoryState,
    GitSessionIdentitySource,
    advance_checkpoint_operation,
    advance_commit_operation,
    build_candidate_record,
    decode_candidate_record,
    decode_checkpoint_operation,
    decode_commit_operation,
    encode_candidate_record,
    encode_checkpoint_operation,
    encode_commit_operation,
)

from tests.git_contract_fixture import exception_graph


_OID = "1" * 40
_TREE = "2" * 40
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _candidate(
    *,
    paths: tuple[GitSessionCandidatePath, ...],
    impact_boundary: tuple[str, ...],
):
    return build_candidate_record(
        session_id="novel",
        branch="story/session/novel",
        branch_tip=_OID,
        expected_tree=_TREE,
        worktree_identity_sha256="c" * 64,
        application_id="application:1",
        proposal_id="proposal:1",
        correlation_id="correlation:1",
        paths=paths,
        proposed_message="Update the opening",
        impact_boundary=impact_boundary,
        author_identity_source=GitSessionIdentitySource.REPOSITORY,
        committer_identity_source=GitSessionIdentitySource.REPOSITORY,
    )


def _commit(
    state: GitSessionHistoryState = GitSessionHistoryState.RUNNING,
    *,
    sequence: int = 0,
) -> GitSessionCommitOperationRecord:
    return GitSessionCommitOperationRecord(
        session_id="novel",
        operation_id="d" * 32,
        candidate_id="e" * 64,
        correlation_id="correlation:commit",
        state=state,
        sequence=sequence,
        expected_branch_tip=_OID,
        commit_id="3" * 40 if state is GitSessionHistoryState.COMMITTED else None,
    )


def _checkpoint(
    state: GitSessionHistoryState = GitSessionHistoryState.RUNNING,
    *,
    sequence: int = 0,
    preserved: bool = False,
) -> GitSessionCheckpointOperationRecord:
    return GitSessionCheckpointOperationRecord(
        session_id="novel",
        operation_id="f" * 32,
        correlation_id="correlation:checkpoint",
        state=state,
        sequence=sequence,
        expected_internal_tip=_TREE,
        expected_tree=_OID,
        commit_intent_sha256=_DIGEST_A,
        checkpoint_id="4" * 40 if state is GitSessionHistoryState.COMMITTED else None,
        alternate_index_preserved=preserved,
    )


class GitSessionHistoryContractTests(unittest.TestCase):
    """Prove candidate identity is deterministic before persistence."""

    def test_exports_constants_enums_and_facade_absence_are_exact(self) -> None:
        expected_exports = (
            "GIT_SESSION_HISTORY_DIRECTORY",
            "GIT_SESSION_HISTORY_MAX_PATHS",
            "GIT_SESSION_HISTORY_MAX_RECORD_BYTES",
            "GIT_SESSION_HISTORY_MAX_TEXT_BYTES",
            "GIT_SESSION_HISTORY_SCHEMA_VERSION",
            "GitSessionCandidatePath",
            "GitSessionCandidatePathOperation",
            "GitSessionCandidateRecord",
            "GitSessionCheckpointOperationRecord",
            "GitSessionCommitOperationRecord",
            "GitSessionHistoryAction",
            "GitSessionHistoryError",
            "GitSessionHistoryFailureCode",
            "GitSessionHistoryInspection",
            "GitSessionHistoryRecoveryAction",
            "GitSessionHistoryState",
            "GitSessionIdentitySource",
            "advance_checkpoint_operation",
            "advance_commit_operation",
            "build_candidate_record",
            "decode_candidate_record",
            "decode_checkpoint_operation",
            "decode_commit_operation",
            "encode_candidate_record",
            "encode_checkpoint_operation",
            "encode_commit_operation",
        )
        self.assertEqual(tuple(contracts_module.__all__), expected_exports)
        self.assertEqual(
            (
                GIT_SESSION_HISTORY_DIRECTORY,
                GIT_SESSION_HISTORY_SCHEMA_VERSION,
                GIT_SESSION_HISTORY_MAX_RECORD_BYTES,
                GIT_SESSION_HISTORY_MAX_PATHS,
                GIT_SESSION_HISTORY_MAX_TEXT_BYTES,
            ),
            (
                "story-writing-agents/runtime/session-history",
                1,
                1024 * 1024,
                4096,
                4096,
            ),
        )
        enums = {
            GitSessionHistoryAction: ("story_commit", "checkpoint"),
            GitSessionHistoryState: (
                "prepared",
                "running",
                "staged",
                "committed",
                "rejected",
                "outcome_unknown",
                "recovery_required",
            ),
            GitSessionCandidatePathOperation: ("create", "update", "delete"),
            GitSessionIdentitySource: ("environment", "repository", "global", "system"),
        }
        for enum_type, values in enums.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)
        for name in expected_exports:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)
        self.assertEqual(len(git_facade.__all__), 22)
        self.assertEqual(len(story_writing_agents.__all__), 104)

    def test_candidate_paths_and_impact_boundary_require_unique_utf8_order(
        self,
    ) -> None:
        character = GitSessionCandidatePath(
            "core_documents/character_sheet.md",
            GitSessionCandidatePathOperation.UPDATE,
            _DIGEST_A,
            _DIGEST_B,
        )
        manuscript = GitSessionCandidatePath(
            "manuscript/part-01/chapter 한글.md",
            GitSessionCandidatePathOperation.CREATE,
            None,
            _DIGEST_A,
        )
        ordered = _candidate(
            paths=(character, manuscript),
            impact_boundary=("characters", "plot"),
        )
        self.assertEqual(
            tuple(item.path for item in ordered.paths),
            tuple(
                sorted(
                    (character.path, manuscript.path),
                    key=lambda value: value.encode("utf-8"),
                )
            ),
        )

        invalid = (
            ((manuscript, character), ("characters", "plot")),
            ((character, manuscript), ("plot", "characters")),
            ((character, manuscript), ("characters", "characters")),
        )
        for paths, impact_boundary in invalid:
            with self.subTest(
                paths=tuple(item.path for item in paths),
                impact_boundary=impact_boundary,
            ):
                with self.assertRaises(GitSessionHistoryError) as caught:
                    _candidate(paths=paths, impact_boundary=impact_boundary)
                self.assertIs(
                    caught.exception.code,
                    GitSessionHistoryFailureCode.RECORD_INVALID,
                )

    def test_records_are_frozen_slotted_exactly_typed_and_content_free(self) -> None:
        candidate = _candidate(
            paths=(
                GitSessionCandidatePath(
                    "manuscript/part-01/chapter-01.md",
                    GitSessionCandidatePathOperation.CREATE,
                    None,
                    _DIGEST_A,
                ),
            ),
            impact_boundary=("plot",),
        )
        inspection = GitSessionHistoryInspection(
            "novel",
            (candidate.candidate_id,),
            (("d" * 32, GitSessionHistoryState.RUNNING),),
            (("f" * 32, GitSessionHistoryState.OUTCOME_UNKNOWN),),
            ("f" * 32,),
        )
        for value in (
            candidate.paths[0],
            candidate,
            _commit(),
            _checkpoint(),
            inspection,
        ):
            with self.subTest(value=type(value).__name__):
                self.assertTrue(is_dataclass(value))
                self.assertTrue(hasattr(value, "__slots__"))
        with self.assertRaises(FrozenInstanceError):
            candidate.session_id = "other"  # type: ignore[misc]
        self.assertEqual(
            tuple(field.name for field in fields(_commit())),
            (
                "session_id",
                "operation_id",
                "candidate_id",
                "correlation_id",
                "state",
                "sequence",
                "expected_branch_tip",
                "commit_id",
                "schema_version",
                "action",
            ),
        )
        for private in ("Update the opening", "plot"):
            self.assertNotIn(private, repr(candidate))

    def test_codecs_are_canonical_bounded_and_reject_invalid_reconstruction(
        self,
    ) -> None:
        candidate = _candidate(
            paths=(
                GitSessionCandidatePath(
                    "manuscript/part-01/chapter 한글.md",
                    GitSessionCandidatePathOperation.CREATE,
                    None,
                    _DIGEST_A,
                ),
            ),
            impact_boundary=("plot",),
        )
        values = (
            (candidate, encode_candidate_record, decode_candidate_record),
            (_commit(), encode_commit_operation, decode_commit_operation),
            (_checkpoint(), encode_checkpoint_operation, decode_checkpoint_operation),
        )
        for value, encoder, decoder in values:
            with self.subTest(value=type(value).__name__):
                payload = encoder(value)
                self.assertEqual(decoder(payload), value)
                self.assertTrue(payload.endswith(b"\n"))
                self.assertEqual(
                    payload,
                    (
                        json.dumps(
                            json.loads(payload),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode(),
                )
                self.assertLessEqual(len(payload), GIT_SESSION_HISTORY_MAX_RECORD_BYTES)

        valid = encode_commit_operation(_commit())
        malformed = (
            b"",
            valid[:-1],
            b" " + valid,
            b'{"action":"story_commit","action":"story_commit"}\n',
            b"\xff\n",
            b"x" * (GIT_SESSION_HISTORY_MAX_RECORD_BYTES + 1),
        )
        for payload in malformed:
            with self.subTest(length=len(payload), prefix=payload[:8]):
                with self.assertRaises(GitSessionHistoryError):
                    decode_commit_operation(payload)

        forged = candidate.paths[0]
        object.__setattr__(forged, "path", "../escape")
        with self.assertRaises(GitSessionHistoryError) as caught:
            encode_candidate_record(candidate)
        self.assertIs(
            caught.exception.code, GitSessionHistoryFailureCode.RECORD_INVALID
        )

    def test_commit_and_checkpoint_transition_tables_are_exhaustive(self) -> None:
        commit_allowed = {
            GitSessionHistoryState.RUNNING: {
                GitSessionHistoryState.STAGED,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            },
            GitSessionHistoryState.STAGED: {
                GitSessionHistoryState.COMMITTED,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            },
            GitSessionHistoryState.OUTCOME_UNKNOWN: {
                GitSessionHistoryState.COMMITTED,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            },
            GitSessionHistoryState.COMMITTED: set(),
            GitSessionHistoryState.REJECTED: set(),
            GitSessionHistoryState.RECOVERY_REQUIRED: set(),
        }
        for current_state, allowed in commit_allowed.items():
            current = _commit(current_state, sequence=7)
            for target in GitSessionHistoryState:
                with self.subTest(
                    action="commit", current=current_state, target=target
                ):
                    if target in allowed:
                        result = advance_commit_operation(
                            current,
                            target,
                            commit_id="3" * 40
                            if target is GitSessionHistoryState.COMMITTED
                            else None,
                        )
                        self.assertEqual((result.state, result.sequence), (target, 8))
                    else:
                        with self.assertRaises(GitSessionHistoryError) as caught:
                            advance_commit_operation(current, target)
                        self.assertIs(
                            caught.exception.code,
                            GitSessionHistoryFailureCode.TRANSITION_INVALID,
                        )

        checkpoint_allowed = {
            GitSessionHistoryState.RUNNING: {
                GitSessionHistoryState.COMMITTED,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            },
            GitSessionHistoryState.OUTCOME_UNKNOWN: {
                GitSessionHistoryState.COMMITTED,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            },
            GitSessionHistoryState.COMMITTED: set(),
            GitSessionHistoryState.RECOVERY_REQUIRED: set(),
        }
        for current_state, allowed in checkpoint_allowed.items():
            current = _checkpoint(current_state, sequence=9)
            for target in GitSessionHistoryState:
                with self.subTest(
                    action="checkpoint", current=current_state, target=target
                ):
                    if target in allowed:
                        result = advance_checkpoint_operation(
                            current,
                            target,
                            checkpoint_id="4" * 40
                            if target is GitSessionHistoryState.COMMITTED
                            else None,
                            alternate_index_preserved=target
                            is GitSessionHistoryState.OUTCOME_UNKNOWN,
                        )
                        self.assertEqual((result.state, result.sequence), (target, 10))
                    else:
                        with self.assertRaises(GitSessionHistoryError) as caught:
                            advance_checkpoint_operation(current, target)
                        self.assertIs(
                            caught.exception.code,
                            GitSessionHistoryFailureCode.TRANSITION_INVALID,
                        )

    def test_errors_are_closed_content_free_and_unlinked(self) -> None:
        for code in GitSessionHistoryFailureCode:
            with self.subTest(code=code.value):
                error = GitSessionHistoryError(
                    code,
                    GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
                )
                self.assertEqual(exception_graph(error), (error,))
                for secret in ("story prose", "/private/path", "provider response"):
                    self.assertNotIn(secret, str(error))
                    self.assertNotIn(secret, repr(error))


if __name__ == "__main__":
    unittest.main()
