"""Closed values, parsing, and errors for session resources."""

from __future__ import annotations

import re

from .contracts import GitIdentity, GitOperationError
from .session_contracts import (
    GIT_SESSION_SEED_MESSAGE_PREFIX,
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionProvisioningJournal,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)


SESSION_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


MAX_SESSION_ADMIN_FILE_BYTES = 64 * 1024


def parse_object_id(value: bytes | str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii").strip()
        except UnicodeError:
            value = ""
    if type(value) is not str or SESSION_OBJECT_ID.fullmatch(value) is None:
        raise resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return value


def decode_utf8(value: bytes) -> str:
    try:
        result = value.decode("utf-8", errors="strict")
    except UnicodeError:
        raise resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    if not result or "\x00" in result or "\r" in result or "\n" in result:
        raise resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return result


def decode_line(value: bytes) -> str:
    return decode_utf8(value.strip())


def parse_identity(value: bytes) -> GitIdentity:
    try:
        text = value.decode("utf-8", errors="strict").strip()
        prefix, _timestamp, _zone = text.rsplit(" ", 2)
        name, email = prefix.rsplit(" <", 1)
        return GitIdentity(name=name, email=email.removesuffix(">"))
    except (UnicodeError, ValueError, GitOperationError):
        raise resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.PROVISION,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None


def seed_payload(journal: GitSessionProvisioningJournal, empty_tree: str) -> bytes:
    timestamp = journal.seed_timestamp_seconds
    author = journal.effective_identity.author
    committer = journal.effective_identity.committer
    return (
        f"tree {empty_tree}\n"
        f"author {author.name} <{author.email}> {timestamp} +0000\n"
        f"committer {committer.name} <{committer.email}> {timestamp} +0000\n"
        "\n"
        f"{GIT_SESSION_SEED_MESSAGE_PREFIX}{journal.session_id}\n"
    ).encode("utf-8", errors="strict")


def valid_seed_payload(
    payload: bytes,
    *,
    identity: GitSessionIdentity,
    empty_tree: str,
    journal: GitSessionProvisioningJournal | None,
) -> bool:
    if journal is not None:
        return payload == seed_payload(journal, empty_tree)
    if len(payload) > MAX_SESSION_ADMIN_FILE_BYTES or b"\x00" in payload or b"\r" in payload:
        return False
    parts = payload.split(b"\n")
    if (
        len(parts) != 6
        or parts[0] != f"tree {empty_tree}".encode("ascii")
        or parts[3] != b""
        or parts[4]
        != f"{GIT_SESSION_SEED_MESSAGE_PREFIX}{identity.session_id}".encode()
        or parts[5] != b""
    ):
        return False
    author = _seed_identity_line(parts[1], b"author ")
    committer = _seed_identity_line(parts[2], b"committer ")
    return author is not None and committer is not None and author[1] == committer[1]


def _seed_identity_line(value: bytes, prefix: bytes) -> tuple[GitIdentity, int] | None:
    if not value.startswith(prefix):
        return None
    try:
        identity, raw_time, zone = value[len(prefix) :].rsplit(b" ", 2)
        if zone != b"+0000":
            return None
        raw_name, raw_email = identity.rsplit(b" <", 1)
        if not raw_email.endswith(b">"):
            return None
        name = raw_name.decode("utf-8", errors="strict")
        email = raw_email[:-1].decode("utf-8", errors="strict")
        timestamp_text = raw_time.decode("ascii")
        timestamp = int(timestamp_text)
        if str(timestamp) != timestamp_text or not 0 <= timestamp <= 2**63 - 1:
            return None
        return GitIdentity(name=name, email=email), timestamp
    except (UnicodeError, ValueError, GitOperationError):
        return None


def decode_lines(value: bytes) -> tuple[str, ...]:
    try:
        return tuple(line.decode("ascii") for line in value.splitlines())
    except UnicodeError:
        raise resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None


def absent(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.ABSENT,
        recovery_action=GitSessionRecoveryAction.CREATE,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def provisioning(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.PROVISIONING,
        recovery_action=GitSessionRecoveryAction.RETRY_CREATE,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def recovery(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.RECOVERY_REQUIRED,
        recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def unknown(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.OUTCOME_UNKNOWN,
        recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def resource_error(
    code: GitSessionFailureCode,
    operation: GitSessionOperationKind,
    state: GitSessionResourceState,
    action: GitSessionRecoveryAction,
) -> GitSessionResourceError:
    error = GitSessionResourceError(
        code,
        operation=operation,
        state=state,
        recovery_action=action,
    )
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


def clear_resource_error(error: GitSessionResourceError) -> GitSessionResourceError:
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


def generation_error() -> GitSessionResourceError:
    return resource_error(
        GitSessionFailureCode.UNSAFE_ROOT,
        GitSessionOperationKind.INSPECT,
        GitSessionResourceState.RECOVERY_REQUIRED,
        GitSessionRecoveryAction.OPERATOR_RECOVERY,
    )
