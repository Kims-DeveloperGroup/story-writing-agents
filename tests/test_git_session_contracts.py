"""Pure acceptance coverage for Git session-resource contracts."""

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git.contracts import GitEffectiveIdentity, GitIdentity
from story_writing_agents.git import session_contracts as contracts_module
from story_writing_agents.git.session_contracts import (
    GIT_SESSION_BRANCH_PREFIX,
    GIT_SESSION_INTERNAL_REF_PREFIX,
    GIT_SESSION_PROVISIONING_DIRECTORY,
    GIT_SESSION_PROVISIONING_LOCK_NAME,
    GIT_SESSION_PROVISIONING_MAX_BYTES,
    GIT_SESSION_PROVISIONING_SCHEMA_VERSION,
    GIT_SESSION_SEED_MESSAGE_PREFIX,
    GIT_SESSION_WORKTREE_SIBLING_SUFFIX,
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionProvisioningJournal,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceInventory,
    GitSessionResourceState,
    decode_session_provisioning_journal,
    encode_session_provisioning_journal,
)

from tests.git_contract_fixture import exception_graph


_OID = "1" * 40
_SEED = "2" * 40
_AUTHOR = GitIdentity("Private Author", "author@example.invalid")
_COMMITTER = GitIdentity("Private Committer", "committer@example.invalid")
_EFFECTIVE = GitEffectiveIdentity(_AUTHOR, _COMMITTER)


def _journal() -> GitSessionProvisioningJournal:
    return GitSessionProvisioningJournal(
        baseline_commit_id=_OID,
        effective_identity=_EFFECTIVE,
        provisioning_id="a" * 32,
        seed_timestamp_seconds=1_780_000_000,
        session_id="novel",
    )


def _ready(identity: GitSessionIdentity) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.READY,
        recovery_action=GitSessionRecoveryAction.NONE,
        baseline_commit_id=_OID,
        seed_commit_id=_SEED,
        initial_branch_tip=_OID,
        current_branch_tip=_OID,
        initial_internal_tip=_SEED,
        current_internal_tip=_SEED,
    )


class GitSessionContractTests(unittest.TestCase):
    """Prove strict values, canonical recovery bytes, and private seams."""

    def test_direct_exports_constants_enums_and_facade_absence_are_exact(self) -> None:
        expected_exports = (
            "GIT_SESSION_BRANCH_PREFIX",
            "GIT_SESSION_INTERNAL_REF_PREFIX",
            "GIT_SESSION_WORKTREE_SIBLING_SUFFIX",
            "GIT_SESSION_PROVISIONING_DIRECTORY",
            "GIT_SESSION_PROVISIONING_LOCK_NAME",
            "GIT_SESSION_PROVISIONING_SCHEMA_VERSION",
            "GIT_SESSION_PROVISIONING_MAX_BYTES",
            "GIT_SESSION_SEED_MESSAGE_PREFIX",
            "GitSessionOperationKind",
            "GitSessionResourceState",
            "GitSessionFailureCode",
            "GitSessionRecoveryAction",
            "GitSessionResourceError",
            "GitSessionIdentity",
            "GitSessionProvisioningJournal",
            "GitSessionResourceInspection",
            "GitSessionResourceInventory",
            "encode_session_provisioning_journal",
            "decode_session_provisioning_journal",
        )
        self.assertEqual(tuple(contracts_module.__all__), expected_exports)
        self.assertEqual(GIT_SESSION_BRANCH_PREFIX, "story/session/")
        self.assertEqual(
            GIT_SESSION_INTERNAL_REF_PREFIX,
            "refs/story-writing-agents/runtime/sessions/",
        )
        self.assertEqual(GIT_SESSION_WORKTREE_SIBLING_SUFFIX, ".story-worktrees")
        self.assertEqual(
            GIT_SESSION_PROVISIONING_DIRECTORY,
            "story-writing-agents/runtime/session-provisioning",
        )
        self.assertEqual(GIT_SESSION_PROVISIONING_LOCK_NAME, ".lock")
        self.assertEqual(GIT_SESSION_PROVISIONING_SCHEMA_VERSION, 1)
        self.assertEqual(GIT_SESSION_PROVISIONING_MAX_BYTES, 64 * 1024)
        self.assertEqual(
            GIT_SESSION_SEED_MESSAGE_PREFIX,
            "Initialize story session runtime: ",
        )
        enums = {
            GitSessionOperationKind: ("inspect", "list", "provision", "require_ready"),
            GitSessionResourceState: (
                "absent",
                "provisioning",
                "ready",
                "outcome_unknown",
                "recovery_required",
            ),
            GitSessionFailureCode: (
                "invalid_request",
                "unsafe_root",
                "lock_unavailable",
                "journal_invalid",
                "resource_collision",
                "mutation_failed",
                "outcome_unknown",
                "recovery_required",
            ),
            GitSessionRecoveryAction: (
                "none",
                "create",
                "retry_create",
                "reopen",
                "operator_recovery",
            ),
        }
        for enum_type, values in enums.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)
        for name in expected_exports:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)
        self.assertEqual(len(git_facade.__all__), 24)
        self.assertEqual(len(story_writing_agents.__all__), 106)

    def test_identity_derives_only_exact_schema_names_for_unicode_root(self) -> None:
        root = Path("/stories/My Story 한글")
        with mock.patch.object(contracts_module.os, "lstat") as lstat:
            identity = GitSessionIdentity(root, "Novel_01")
        lstat.assert_not_called()
        self.assertEqual(identity.main_root, root)
        self.assertEqual(identity.branch, "story/session/Novel_01")
        self.assertEqual(
            identity.internal_ref,
            "refs/story-writing-agents/runtime/sessions/Novel_01",
        )
        self.assertEqual(
            identity.worktree_root,
            Path("/stories/My Story 한글.story-worktrees/Novel_01"),
        )
        for invalid in ("", "-bad", "a" * 65, "has space", "한글", "a/b", 7):
            with self.subTest(value=repr(invalid)):
                with self.assertRaises(GitSessionResourceError) as caught:
                    GitSessionIdentity(root, invalid)  # type: ignore[arg-type]
                self.assertIs(
                    caught.exception.code, GitSessionFailureCode.INVALID_REQUEST
                )

    def test_journal_bytes_are_exact_canonical_utf8_and_round_trip(self) -> None:
        journal = _journal()
        payload = encode_session_provisioning_journal(journal)
        expected = (
            json.dumps(
                {
                    "baseline_commit_id": _OID,
                    "effective_identity": {
                        "author": {
                            "email": "author@example.invalid",
                            "name": "Private Author",
                        },
                        "committer": {
                            "email": "committer@example.invalid",
                            "name": "Private Committer",
                        },
                    },
                    "provisioning_id": "a" * 32,
                    "schema_version": 1,
                    "seed_timestamp_seconds": 1_780_000_000,
                    "session_id": "novel",
                    "state": "provisioning",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        self.assertEqual(payload, expected)
        self.assertEqual(decode_session_provisioning_journal(payload), journal)
        self.assertLessEqual(len(payload), GIT_SESSION_PROVISIONING_MAX_BYTES)

    def test_journal_decoder_rejects_noncanonical_malformed_and_oversized_bytes(
        self,
    ) -> None:
        valid = encode_session_provisioning_journal(_journal())
        decoded = json.loads(valid)
        malformed = [
            b"",
            valid[:-1],
            b" " + valid,
            valid.replace(b'"schema_version":1', b'"schema_version":true'),
            valid.replace(b'"state":"provisioning"', b'"state":"ready"'),
            valid.replace(b'"provisioning_id":"', b'"provisioning_id":"A'),
            valid.replace(b'"session_id":"novel"', b'"session_id":"bad/id"'),
            b'{"schema_version":1,"schema_version":1}\n',
            b"\xff\n",
            b"x" * (GIT_SESSION_PROVISIONING_MAX_BYTES + 1),
        ]
        extra = dict(decoded)
        extra["extra"] = None
        malformed.append(
            (json.dumps(extra, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        missing = dict(decoded)
        missing.pop("state")
        malformed.append(
            (json.dumps(missing, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        for payload in malformed:
            with self.subTest(length=len(payload), prefix=payload[:16]):
                with self.assertRaises(GitSessionResourceError) as caught:
                    decode_session_provisioning_journal(payload)
                self.assertIs(
                    caught.exception.code, GitSessionFailureCode.JOURNAL_INVALID
                )
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))

    def test_contracts_are_frozen_slotted_and_deeply_revalidated(self) -> None:
        identity = GitSessionIdentity(Path("/stories/example"), "novel")
        journal = _journal()
        ready = _ready(identity)
        inventory = GitSessionResourceInventory(_OID, (ready,))
        for value in (identity, journal, ready, inventory):
            with self.subTest(contract=type(value).__name__):
                self.assertTrue(is_dataclass(value))
                self.assertTrue(hasattr(value, "__slots__"))
        with self.assertRaises(FrozenInstanceError):
            journal.session_id = "changed"  # type: ignore[misc]
        object.__setattr__(journal, "provisioning_id", "forged")
        with self.assertRaises(GitSessionResourceError) as caught:
            encode_session_provisioning_journal(journal)
        self.assertIs(caught.exception.code, GitSessionFailureCode.JOURNAL_INVALID)
        object.__setattr__(ready.identity, "branch", "story/session/other")
        with self.assertRaises(GitSessionResourceError):
            GitSessionResourceInventory(_OID, (ready,))

    def test_inspection_states_enforce_exact_coherent_fields(self) -> None:
        identity = GitSessionIdentity(Path("/stories/example"), "novel")
        absent = GitSessionResourceInspection(
            identity,
            GitSessionResourceState.ABSENT,
            GitSessionRecoveryAction.CREATE,
            _OID,
            None,
            None,
            None,
            None,
            None,
        )
        self.assertIs(absent.state, GitSessionResourceState.ABSENT)
        self.assertEqual(
            tuple(field.name for field in fields(absent)),
            (
                "identity",
                "state",
                "recovery_action",
                "baseline_commit_id",
                "seed_commit_id",
                "initial_branch_tip",
                "current_branch_tip",
                "initial_internal_tip",
                "current_internal_tip",
            ),
        )
        self.assertIs(_ready(identity).state, GitSessionResourceState.READY)
        with self.assertRaises(GitSessionResourceError):
            GitSessionResourceInspection(
                identity,
                GitSessionResourceState.ABSENT,
                GitSessionRecoveryAction.NONE,
                _OID,
                None,
                None,
                None,
                None,
                None,
            )
        with self.assertRaises(GitSessionResourceError):
            GitSessionResourceInspection(
                identity,
                GitSessionResourceState.READY,
                GitSessionRecoveryAction.NONE,
                _OID,
                None,
                _OID,
                _OID,
                _SEED,
                _SEED,
            )

    def test_errors_and_representations_never_expose_identity_or_caller_content(
        self,
    ) -> None:
        journal = _journal()
        for private in (
            "Private Author",
            "Private Committer",
            "author@example.invalid",
            "committer@example.invalid",
        ):
            self.assertNotIn(private, repr(journal))
        for code in GitSessionFailureCode:
            with self.subTest(code=code.value):
                error = GitSessionResourceError(
                    code,
                    operation=GitSessionOperationKind.INSPECT,
                    state=GitSessionResourceState.RECOVERY_REQUIRED,
                    recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
                self.assertNotIn("private caller bytes", str(error))
                self.assertEqual(exception_graph(error), (error,))


if __name__ == "__main__":
    unittest.main()
