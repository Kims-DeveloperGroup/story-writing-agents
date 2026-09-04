"""Pure contracts for exact local Git resources owned by one story session."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .contracts import GitEffectiveIdentity, GitIdentity


GIT_SESSION_BRANCH_PREFIX = "story/session/"
GIT_SESSION_INTERNAL_REF_PREFIX = "refs/story-writing-agents/runtime/sessions/"
GIT_SESSION_WORKTREE_SIBLING_SUFFIX = ".story-worktrees"
GIT_SESSION_PROVISIONING_DIRECTORY = "story-writing-agents/runtime/session-provisioning"
GIT_SESSION_PROVISIONING_LOCK_NAME = ".lock"
GIT_SESSION_PROVISIONING_SCHEMA_VERSION = 1
GIT_SESSION_PROVISIONING_MAX_BYTES = 64 * 1024
GIT_SESSION_SEED_MESSAGE_PREFIX = "Initialize story session runtime: "

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_PROVISIONING_ID = re.compile(r"[0-9a-f]{32}")
_JOURNAL_FIELDS = frozenset(
    {
        "baseline_commit_id",
        "effective_identity",
        "provisioning_id",
        "schema_version",
        "seed_timestamp_seconds",
        "session_id",
        "state",
    }
)


class GitSessionOperationKind(StrEnum):
    INSPECT = "inspect"
    LIST = "list"
    PROVISION = "provision"
    REQUIRE_READY = "require_ready"


class GitSessionResourceState(StrEnum):
    ABSENT = "absent"
    PROVISIONING = "provisioning"
    READY = "ready"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class GitSessionFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNSAFE_ROOT = "unsafe_root"
    LOCK_UNAVAILABLE = "lock_unavailable"
    JOURNAL_INVALID = "journal_invalid"
    RESOURCE_COLLISION = "resource_collision"
    MUTATION_FAILED = "mutation_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class GitSessionRecoveryAction(StrEnum):
    NONE = "none"
    CREATE = "create"
    RETRY_CREATE = "retry_create"
    REOPEN = "reopen"
    OPERATOR_RECOVERY = "operator_recovery"


_DIAGNOSTICS = {
    GitSessionFailureCode.INVALID_REQUEST: "Git session request is invalid.",
    GitSessionFailureCode.UNSAFE_ROOT: "Git session root is unsafe.",
    GitSessionFailureCode.LOCK_UNAVAILABLE: "Git session repository lock is unavailable.",
    GitSessionFailureCode.JOURNAL_INVALID: "Git session journal is invalid.",
    GitSessionFailureCode.RESOURCE_COLLISION: "Git session resource identity collides.",
    GitSessionFailureCode.MUTATION_FAILED: "Git session mutation failed.",
    GitSessionFailureCode.OUTCOME_UNKNOWN: "Git session mutation outcome is unknown.",
    GitSessionFailureCode.RECOVERY_REQUIRED: "Git session resources require recovery.",
}


class GitSessionResourceError(RuntimeError):
    """One content-free failure at the session-resource boundary."""

    def __init__(
        self,
        code: GitSessionFailureCode,
        *,
        operation: GitSessionOperationKind,
        state: GitSessionResourceState,
        recovery_action: GitSessionRecoveryAction,
    ) -> None:
        if (
            type(code) is not GitSessionFailureCode
            or type(operation) is not GitSessionOperationKind
            or type(state) is not GitSessionResourceState
            or type(recovery_action) is not GitSessionRecoveryAction
        ):
            raise TypeError("Git session error facts are invalid")
        self.code = code
        self.operation = operation
        self.state = state
        self.recovery_action = recovery_action
        super().__init__(_DIAGNOSTICS[code])


@dataclass(frozen=True, slots=True)
class GitSessionIdentity:
    """One schema-derived branch/ref/sibling-worktree identity."""

    main_root: Path = field(repr=False)
    session_id: str
    branch: str = field(init=False)
    internal_ref: str = field(init=False)
    worktree_root: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.main_root, Path) or not self.main_root.is_absolute():
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        normalized = Path(os.path.normpath(os.fspath(self.main_root)))
        if normalized != self.main_root or self.main_root.name in {"", ".", ".."}:
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        session = _require_session_id(self.session_id)
        object.__setattr__(self, "branch", f"{GIT_SESSION_BRANCH_PREFIX}{session}")
        object.__setattr__(
            self,
            "internal_ref",
            f"{GIT_SESSION_INTERNAL_REF_PREFIX}{session}",
        )
        sibling = self.main_root.with_name(
            f"{self.main_root.name}{GIT_SESSION_WORKTREE_SIBLING_SUFFIX}"
        )
        object.__setattr__(self, "worktree_root", sibling / session)


@dataclass(frozen=True, slots=True)
class GitSessionProvisioningJournal:
    """Immutable provisioning identity persisted before the first Git mutation."""

    baseline_commit_id: str
    effective_identity: GitEffectiveIdentity = field(repr=False)
    provisioning_id: str
    seed_timestamp_seconds: int
    session_id: str
    schema_version: int = GIT_SESSION_PROVISIONING_SCHEMA_VERSION
    state: str = "provisioning"

    def __post_init__(self) -> None:
        _require_object_id(self.baseline_commit_id)
        _reconstruct_identity(self.effective_identity)
        if (
            type(self.provisioning_id) is not str
            or _PROVISIONING_ID.fullmatch(self.provisioning_id) is None
            or type(self.seed_timestamp_seconds) is not int
            or self.seed_timestamp_seconds < 0
            or self.seed_timestamp_seconds > 2**63 - 1
            or type(self.schema_version) is not int
            or self.schema_version != GIT_SESSION_PROVISIONING_SCHEMA_VERSION
            or self.state != "provisioning"
        ):
            raise _error(GitSessionFailureCode.JOURNAL_INVALID)
        _require_session_id(self.session_id)


@dataclass(frozen=True, slots=True)
class GitSessionResourceInspection:
    """Bounded content-free facts for one requested session triple."""

    identity: GitSessionIdentity
    state: GitSessionResourceState
    recovery_action: GitSessionRecoveryAction
    baseline_commit_id: str | None
    seed_commit_id: str | None
    initial_branch_tip: str | None
    current_branch_tip: str | None
    initial_internal_tip: str | None
    current_internal_tip: str | None

    def __post_init__(self) -> None:
        _reconstruct_session_identity(self.identity)
        if (
            type(self.state) is not GitSessionResourceState
            or type(self.recovery_action) is not GitSessionRecoveryAction
        ):
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        for value in (
            self.baseline_commit_id,
            self.seed_commit_id,
            self.initial_branch_tip,
            self.current_branch_tip,
            self.initial_internal_tip,
            self.current_internal_tip,
        ):
            if value is not None:
                _require_object_id(value)
        object_ids = tuple(
            value
            for value in (
                self.baseline_commit_id,
                self.seed_commit_id,
                self.initial_branch_tip,
                self.current_branch_tip,
                self.initial_internal_tip,
                self.current_internal_tip,
            )
            if value is not None
        )
        if len({len(value) for value in object_ids}) > 1:
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        if self.state is GitSessionResourceState.ABSENT:
            if (
                any(
                    value is not None
                    for value in (
                        self.seed_commit_id,
                        self.initial_branch_tip,
                        self.current_branch_tip,
                        self.initial_internal_tip,
                        self.current_internal_tip,
                    )
                )
                or self.recovery_action is not GitSessionRecoveryAction.CREATE
            ):
                raise _error(GitSessionFailureCode.INVALID_REQUEST)
        elif self.state is GitSessionResourceState.READY:
            if any(
                value is None
                for value in (
                    self.baseline_commit_id,
                    self.seed_commit_id,
                    self.initial_branch_tip,
                    self.current_branch_tip,
                    self.initial_internal_tip,
                    self.current_internal_tip,
                )
            ) or self.recovery_action not in {
                GitSessionRecoveryAction.NONE,
                GitSessionRecoveryAction.REOPEN,
            }:
                raise _error(GitSessionFailureCode.INVALID_REQUEST)
            if (
                self.initial_branch_tip != self.baseline_commit_id
                or self.initial_internal_tip != self.seed_commit_id
            ):
                raise _error(GitSessionFailureCode.INVALID_REQUEST)
        elif self.state is GitSessionResourceState.PROVISIONING:
            if (
                self.recovery_action is not GitSessionRecoveryAction.RETRY_CREATE
                or self.baseline_commit_id is None
            ):
                raise _error(GitSessionFailureCode.INVALID_REQUEST)
        elif self.recovery_action is not GitSessionRecoveryAction.OPERATOR_RECOVERY:
            raise _error(GitSessionFailureCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class GitSessionResourceInventory:
    """Exhaustive correlated session facts for one main repository."""

    baseline_commit_id: str
    sessions: tuple[GitSessionResourceInspection, ...]

    def __post_init__(self) -> None:
        _require_object_id(self.baseline_commit_id)
        if type(self.sessions) is not tuple:
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        sessions = tuple(_reconstruct_inspection(value) for value in self.sessions)
        ordered = tuple(value.identity.session_id for value in self.sessions)
        if ordered != tuple(sorted(ordered, key=lambda value: value.encode("ascii"))):
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        if len(set(ordered)) != len(ordered):
            raise _error(GitSessionFailureCode.INVALID_REQUEST)
        if sessions != self.sessions:
            raise _error(GitSessionFailureCode.INVALID_REQUEST)


def encode_session_provisioning_journal(
    journal: GitSessionProvisioningJournal,
) -> bytes:
    """Encode one deeply revalidated exact journal."""

    validated = _reconstruct_journal(journal)
    value = {
        "baseline_commit_id": validated.baseline_commit_id,
        "effective_identity": {
            "author": {
                "email": validated.effective_identity.author.email,
                "name": validated.effective_identity.author.name,
            },
            "committer": {
                "email": validated.effective_identity.committer.email,
                "name": validated.effective_identity.committer.name,
            },
        },
        "provisioning_id": validated.provisioning_id,
        "schema_version": validated.schema_version,
        "seed_timestamp_seconds": validated.seed_timestamp_seconds,
        "session_id": validated.session_id,
        "state": validated.state,
    }
    payload: bytes | None = None
    try:
        payload = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        pass
    if payload is None or len(payload) > GIT_SESSION_PROVISIONING_MAX_BYTES:
        raise _error(GitSessionFailureCode.JOURNAL_INVALID)
    return payload


def decode_session_provisioning_journal(
    payload: bytes,
) -> GitSessionProvisioningJournal:
    """Decode only the byte-exact canonical journal representation."""

    if (
        type(payload) is not bytes
        or not 0 < len(payload) <= GIT_SESSION_PROVISIONING_MAX_BYTES
    ):
        raise _error(GitSessionFailureCode.JOURNAL_INVALID)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    journal: GitSessionProvisioningJournal | None = None
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
        )
        if type(value) is not dict or frozenset(value) != _JOURNAL_FIELDS:
            raise ValueError("shape")
        raw_identity = value["effective_identity"]
        if type(raw_identity) is not dict or frozenset(raw_identity) != {
            "author",
            "committer",
        }:
            raise ValueError("identity")
        identities: dict[str, GitIdentity] = {}
        for role in ("author", "committer"):
            raw = raw_identity[role]
            if type(raw) is not dict or frozenset(raw) != {"email", "name"}:
                raise ValueError("identity")
            identities[role] = GitIdentity(name=raw["name"], email=raw["email"])
        journal = GitSessionProvisioningJournal(
            baseline_commit_id=value["baseline_commit_id"],
            effective_identity=GitEffectiveIdentity(
                author=identities["author"],
                committer=identities["committer"],
            ),
            provisioning_id=value["provisioning_id"],
            schema_version=value["schema_version"],
            seed_timestamp_seconds=value["seed_timestamp_seconds"],
            session_id=value["session_id"],
            state=value["state"],
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        GitSessionResourceError,
    ):
        pass
    if journal is None:
        raise _error(GitSessionFailureCode.JOURNAL_INVALID)
    if encode_session_provisioning_journal(journal) != payload:
        raise _error(GitSessionFailureCode.JOURNAL_INVALID)
    return journal


def _require_session_id(value: object) -> str:
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise _error(GitSessionFailureCode.INVALID_REQUEST)
    return value


def _require_object_id(value: object) -> str:
    if type(value) is not str or _OBJECT_ID.fullmatch(value) is None:
        raise _error(GitSessionFailureCode.INVALID_REQUEST)
    return value


def _reconstruct_identity(value: object) -> GitEffectiveIdentity:
    result: GitEffectiveIdentity | None = None
    try:
        if type(value) is not GitEffectiveIdentity:
            raise TypeError
        result = GitEffectiveIdentity(
            author=GitIdentity(name=value.author.name, email=value.author.email),
            committer=GitIdentity(
                name=value.committer.name, email=value.committer.email
            ),
        )
    except (AttributeError, TypeError, RuntimeError):
        pass
    if result is None:
        raise _error(GitSessionFailureCode.JOURNAL_INVALID)
    return result


def _reconstruct_session_identity(value: object) -> GitSessionIdentity:
    result: GitSessionIdentity | None = None
    try:
        if type(value) is not GitSessionIdentity:
            raise TypeError
        result = GitSessionIdentity(
            main_root=value.main_root, session_id=value.session_id
        )
        if (
            value.branch != result.branch
            or value.internal_ref != result.internal_ref
            or value.worktree_root != result.worktree_root
        ):
            result = None
            raise TypeError
    except (AttributeError, TypeError, RuntimeError):
        pass
    if result is None:
        raise _error(GitSessionFailureCode.INVALID_REQUEST)
    return result


def _reconstruct_journal(value: object) -> GitSessionProvisioningJournal:
    result: GitSessionProvisioningJournal | None = None
    try:
        if type(value) is not GitSessionProvisioningJournal:
            raise TypeError
        result = GitSessionProvisioningJournal(
            baseline_commit_id=value.baseline_commit_id,
            effective_identity=_reconstruct_identity(value.effective_identity),
            provisioning_id=value.provisioning_id,
            seed_timestamp_seconds=value.seed_timestamp_seconds,
            session_id=value.session_id,
            schema_version=value.schema_version,
            state=value.state,
        )
    except (AttributeError, TypeError, RuntimeError):
        pass
    if result is None:
        raise _error(GitSessionFailureCode.JOURNAL_INVALID)
    return result


def _reconstruct_inspection(value: object) -> GitSessionResourceInspection:
    result: GitSessionResourceInspection | None = None
    try:
        if type(value) is not GitSessionResourceInspection:
            raise TypeError
        result = GitSessionResourceInspection(
            identity=_reconstruct_session_identity(value.identity),
            state=value.state,
            recovery_action=value.recovery_action,
            baseline_commit_id=value.baseline_commit_id,
            seed_commit_id=value.seed_commit_id,
            initial_branch_tip=value.initial_branch_tip,
            current_branch_tip=value.current_branch_tip,
            initial_internal_tip=value.initial_internal_tip,
            current_internal_tip=value.current_internal_tip,
        )
    except (AttributeError, TypeError, RuntimeError):
        pass
    if result is None:
        raise _error(GitSessionFailureCode.INVALID_REQUEST)
    return result


def _error(code: GitSessionFailureCode) -> GitSessionResourceError:
    state = (
        GitSessionResourceState.OUTCOME_UNKNOWN
        if code is GitSessionFailureCode.OUTCOME_UNKNOWN
        else GitSessionResourceState.RECOVERY_REQUIRED
    )
    error = GitSessionResourceError(
        code,
        operation=GitSessionOperationKind.INSPECT,
        state=state,
        recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
    )
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
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
]
