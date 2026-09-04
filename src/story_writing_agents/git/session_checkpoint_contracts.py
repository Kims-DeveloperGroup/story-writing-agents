"""Pure contracts for exact session-owned internal-ref checkpoints."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum


SESSION_CHECKPOINT_MAX_PATHS = 4096
SESSION_CHECKPOINT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
SESSION_CHECKPOINT_MESSAGE_PREFIX = "Checkpoint story session runtime: "

_MAX_CONTENT_BYTES = 8 * 1024 * 1024
_MAX_PATH_BYTES = 4096
_SAFE_IDENTIFIER_TEXT = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_SAFE_IDENTIFIER = re.compile(_SAFE_IDENTIFIER_TEXT)
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class SessionCheckpointOwner(StrEnum):
    """Closed runtime components allowed to supply checkpoint snapshots."""

    WORKSPACE = "workspace"
    CLI_CONTEXT = "cli_context"
    BACKEND = "backend"
    CONVERSATION_CATALOG = "conversation_catalog"
    COLLABORATION_HISTORY = "collaboration_history"


class SessionCheckpointPathFamily(StrEnum):
    """Closed path families admitted to one session checkpoint."""

    SESSION_OVERRIDE = "session_override"
    CONVERSATION_DEFAULT = "conversation_default"
    PROVIDER_SESSION = "provider_session"
    CONTEXTUAL_PROVIDER_SESSION = "contextual_provider_session"
    AGENT_DEBATE_STATE = "agent_debate_state"
    DEBATE_LOG = "debate_log"
    DEBATE_STATE = "debate_state"
    DEBATE_RESULT = "debate_result"
    DEBATE_CONTEXT = "debate_context"
    CONVERSATION_METADATA = "conversation_metadata"
    COLLABORATION_HISTORY = "collaboration_history"


class SessionCheckpointPathOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class SessionCheckpointState(StrEnum):
    UNCHANGED = "unchanged"
    RUNNING = "running"
    COMMITTED = "committed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class SessionCheckpointFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    PATH_NOT_ALLOWED = "path_not_allowed"
    SNAPSHOT_INVALID = "snapshot_invalid"
    SNAPSHOT_DRIFT = "snapshot_drift"
    SESSION_NOT_READY = "session_not_ready"
    HISTORY_FAILED = "history_failed"
    GIT_FAILED = "git_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class SessionCheckpointRecoveryAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    INSPECT = "inspect"
    OPERATOR_RECOVERY = "operator_recovery"


_DIAGNOSTICS = {
    SessionCheckpointFailureCode.INVALID_REQUEST: "Checkpoint request is invalid.",
    SessionCheckpointFailureCode.PATH_NOT_ALLOWED: "Checkpoint path is not allowed.",
    SessionCheckpointFailureCode.SNAPSHOT_INVALID: "Checkpoint snapshot is invalid.",
    SessionCheckpointFailureCode.SNAPSHOT_DRIFT: "Checkpoint snapshot changed.",
    SessionCheckpointFailureCode.SESSION_NOT_READY: "Checkpoint session is not ready.",
    SessionCheckpointFailureCode.HISTORY_FAILED: "Checkpoint history failed.",
    SessionCheckpointFailureCode.GIT_FAILED: "Checkpoint Git operation failed.",
    SessionCheckpointFailureCode.OUTCOME_UNKNOWN: "Checkpoint outcome is unknown.",
    SessionCheckpointFailureCode.RECOVERY_REQUIRED: "Checkpoint requires recovery.",
}


class SessionCheckpointError(RuntimeError):
    """One fixed content-free checkpoint failure."""

    __slots__ = ("code", "recovery_action")

    def __init__(
        self,
        code: SessionCheckpointFailureCode,
        recovery_action: SessionCheckpointRecoveryAction,
    ) -> None:
        if (
            type(code) is not SessionCheckpointFailureCode
            or type(recovery_action) is not SessionCheckpointRecoveryAction
        ):
            raise TypeError("checkpoint error facts are invalid")
        self.code = code
        self.recovery_action = recovery_action
        super().__init__(_DIAGNOSTICS[code])


@dataclass(frozen=True, slots=True)
class SessionCheckpointPathClassification:
    """One exact allowlisted path family and its required runtime owner."""

    owner: SessionCheckpointOwner
    family: SessionCheckpointPathFamily

    def __post_init__(self) -> None:
        if (
            type(self.owner) is not SessionCheckpointOwner
            or type(self.family) is not SessionCheckpointPathFamily
            or _FAMILY_OWNERS[self.family] is not self.owner
        ):
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class SessionCheckpointSnapshot:
    """One immutable owner-supplied create, update, or delete delta."""

    owner: SessionCheckpointOwner
    path: str
    operation: SessionCheckpointPathOperation
    before_sha256: str | None
    after_sha256: str | None
    content: bytes | None = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.owner) is not SessionCheckpointOwner
            or type(self.operation) is not SessionCheckpointPathOperation
        ):
            raise _error(SessionCheckpointFailureCode.SNAPSHOT_INVALID)
        classified = _classify(self.path, expected_session=None)
        if classified.owner is not self.owner:
            raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED)
        before = _optional_digest(self.before_sha256)
        after = _optional_digest(self.after_sha256)
        content = self.content
        if self.operation is SessionCheckpointPathOperation.CREATE:
            valid = before is None and after is not None and type(content) is bytes
        elif self.operation is SessionCheckpointPathOperation.UPDATE:
            valid = (
                before is not None
                and after is not None
                and before != after
                and type(content) is bytes
            )
        else:
            valid = before is not None and after is None and content is None
        if not valid:
            raise _error(SessionCheckpointFailureCode.SNAPSHOT_INVALID)
        if content is not None and (
            len(content) > _MAX_CONTENT_BYTES
            or hashlib.sha256(content).hexdigest() != after
        ):
            raise _error(SessionCheckpointFailureCode.SNAPSHOT_INVALID)


@dataclass(frozen=True, slots=True)
class SessionCheckpointRequest:
    """One ordered bounded set of owner-supplied checkpoint deltas."""

    session_id: str
    correlation_id: str
    snapshots: tuple[SessionCheckpointSnapshot, ...]

    def __post_init__(self) -> None:
        session = _require_identifier(self.session_id)
        _require_opaque(self.correlation_id)
        if (
            type(self.snapshots) is not tuple
            or len(self.snapshots) > SESSION_CHECKPOINT_MAX_PATHS
            or any(
                type(value) is not SessionCheckpointSnapshot for value in self.snapshots
            )
        ):
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
        snapshots = tuple(_reconstruct_snapshot(value) for value in self.snapshots)
        paths = tuple(value.path for value in snapshots)
        if (
            len(set(paths)) != len(paths)
            or paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8")))
            or sum(len(value.content or b"") for value in snapshots)
            > SESSION_CHECKPOINT_MAX_TOTAL_BYTES
        ):
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
        for value in snapshots:
            classification = classify_session_checkpoint_path(value.path, session)
            if classification.owner is not value.owner:
                raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED)


@dataclass(frozen=True, slots=True)
class SessionCheckpointResult:
    """One content-free exact checkpoint or restart classification."""

    session_id: str
    correlation_id: str
    state: SessionCheckpointState
    recovery_action: SessionCheckpointRecoveryAction
    internal_tip: str
    operation_id: str | None = None
    checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.session_id)
        _require_opaque(self.correlation_id)
        tip = _require_object_id(self.internal_tip)
        operation = _optional_operation_id(self.operation_id)
        checkpoint = _optional_object_id(self.checkpoint_id)
        if (
            type(self.state) is not SessionCheckpointState
            or type(self.recovery_action) is not SessionCheckpointRecoveryAction
            or (checkpoint is not None and len(checkpoint) != len(tip))
        ):
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
        expected = {
            SessionCheckpointState.UNCHANGED: (
                SessionCheckpointRecoveryAction.NONE,
                False,
                False,
            ),
            SessionCheckpointState.RUNNING: (
                SessionCheckpointRecoveryAction.INSPECT,
                True,
                False,
            ),
            SessionCheckpointState.COMMITTED: (
                SessionCheckpointRecoveryAction.NONE,
                True,
                True,
            ),
            SessionCheckpointState.OUTCOME_UNKNOWN: (
                SessionCheckpointRecoveryAction.INSPECT,
                True,
                False,
            ),
            SessionCheckpointState.RECOVERY_REQUIRED: (
                SessionCheckpointRecoveryAction.OPERATOR_RECOVERY,
                True,
                False,
            ),
        }[self.state]
        if (
            self.recovery_action is not expected[0]
            or (operation is not None) is not expected[1]
            or (checkpoint is not None) is not expected[2]
            or (self.state is SessionCheckpointState.COMMITTED and checkpoint != tip)
        ):
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)


_FAMILY_OWNERS = {
    SessionCheckpointPathFamily.SESSION_OVERRIDE: SessionCheckpointOwner.WORKSPACE,
    SessionCheckpointPathFamily.CONVERSATION_DEFAULT: SessionCheckpointOwner.CLI_CONTEXT,
    SessionCheckpointPathFamily.PROVIDER_SESSION: SessionCheckpointOwner.BACKEND,
    SessionCheckpointPathFamily.CONTEXTUAL_PROVIDER_SESSION: SessionCheckpointOwner.BACKEND,
    SessionCheckpointPathFamily.AGENT_DEBATE_STATE: SessionCheckpointOwner.WORKSPACE,
    SessionCheckpointPathFamily.DEBATE_LOG: SessionCheckpointOwner.WORKSPACE,
    SessionCheckpointPathFamily.DEBATE_STATE: SessionCheckpointOwner.WORKSPACE,
    SessionCheckpointPathFamily.DEBATE_RESULT: SessionCheckpointOwner.WORKSPACE,
    SessionCheckpointPathFamily.DEBATE_CONTEXT: SessionCheckpointOwner.WORKSPACE,
    SessionCheckpointPathFamily.CONVERSATION_METADATA: SessionCheckpointOwner.CONVERSATION_CATALOG,
    SessionCheckpointPathFamily.COLLABORATION_HISTORY: SessionCheckpointOwner.COLLABORATION_HISTORY,
}

_PATH_PATTERNS = (
    (
        re.compile(
            rf"agent_states/orchestrator_state/sessions/(?P<S>{_SAFE_IDENTIFIER_TEXT})\.json"
        ),
        SessionCheckpointPathFamily.SESSION_OVERRIDE,
    ),
    (
        re.compile(
            rf"agent_states/orchestrator_state/conversation_defaults/(?P<S>{_SAFE_IDENTIFIER_TEXT})\.json"
        ),
        SessionCheckpointPathFamily.CONVERSATION_DEFAULT,
    ),
    (
        re.compile(
            rf"agent_states/orchestrator_state/provider_sessions/(?P<S>{_SAFE_IDENTIFIER_TEXT})\.json"
        ),
        SessionCheckpointPathFamily.PROVIDER_SESSION,
    ),
    (
        re.compile(
            rf"agent_states/orchestrator_state/provider_sessions/\.contextual/v1/(?P<S>{_SAFE_IDENTIFIER_TEXT})/(?P<H>[0-9a-f]{{64}})\.json"
        ),
        SessionCheckpointPathFamily.CONTEXTUAL_PROVIDER_SESSION,
    ),
    (
        re.compile(
            rf"agent_states/(?P<OWNER>orchestrator_state|plot_architect_state|character_custodian_state|execution_specialist_state)/debates/(?P<S>{_SAFE_IDENTIFIER_TEXT})/(?P<D>{_SAFE_IDENTIFIER_TEXT})\.jsonl"
        ),
        SessionCheckpointPathFamily.AGENT_DEBATE_STATE,
    ),
    (
        re.compile(
            rf"discussion_logs/(?P<S>{_SAFE_IDENTIFIER_TEXT})/(?P<D>{_SAFE_IDENTIFIER_TEXT})\.jsonl"
        ),
        SessionCheckpointPathFamily.DEBATE_LOG,
    ),
    (
        re.compile(
            rf"discussion_logs/(?P<S>{_SAFE_IDENTIFIER_TEXT})/(?P<D>{_SAFE_IDENTIFIER_TEXT})\.state\.json"
        ),
        SessionCheckpointPathFamily.DEBATE_STATE,
    ),
    (
        re.compile(
            rf"discussion_logs/(?P<S>{_SAFE_IDENTIFIER_TEXT})/(?P<D>{_SAFE_IDENTIFIER_TEXT})\.result\.json"
        ),
        SessionCheckpointPathFamily.DEBATE_RESULT,
    ),
    (
        re.compile(
            rf"discussion_logs/(?P<S>{_SAFE_IDENTIFIER_TEXT})/(?P<D>{_SAFE_IDENTIFIER_TEXT})\.context\.json"
        ),
        SessionCheckpointPathFamily.DEBATE_CONTEXT,
    ),
    (
        re.compile(
            rf"discussion_logs/(?P<S>{_SAFE_IDENTIFIER_TEXT})/interactive/conversations/(?P<C>{_SAFE_IDENTIFIER_TEXT})/metadata\.json"
        ),
        SessionCheckpointPathFamily.CONVERSATION_METADATA,
    ),
    (
        re.compile(
            rf"discussion_logs/(?P<S>{_SAFE_IDENTIFIER_TEXT})/interactive/conversations/(?P<C>{_SAFE_IDENTIFIER_TEXT})/collaboration-history\.jsonl"
        ),
        SessionCheckpointPathFamily.COLLABORATION_HISTORY,
    ),
)


def classify_session_checkpoint_path(
    path: str,
    session_id: str,
) -> SessionCheckpointPathClassification:
    """Classify one exact allowlisted path for the supplied story session."""

    return _classify(path, expected_session=_require_identifier(session_id))


def _classify(
    path: object,
    *,
    expected_session: str | None,
) -> SessionCheckpointPathClassification:
    value = _require_path(path)
    for pattern, family in _PATH_PATTERNS:
        matched = pattern.fullmatch(value)
        if matched is None:
            continue
        if expected_session is not None and matched.group("S") != expected_session:
            raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED)
        return SessionCheckpointPathClassification(_FAMILY_OWNERS[family], family)
    raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED)


def _reconstruct_snapshot(value: object) -> SessionCheckpointSnapshot:
    try:
        if type(value) is not SessionCheckpointSnapshot:
            raise TypeError
        return SessionCheckpointSnapshot(
            owner=value.owner,
            path=value.path,
            operation=value.operation,
            before_sha256=value.before_sha256,
            after_sha256=value.after_sha256,
            content=value.content,
        )
    except (AttributeError, TypeError, SessionCheckpointError):
        raise _error(SessionCheckpointFailureCode.SNAPSHOT_INVALID) from None


def _require_identifier(value: object) -> str:
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
    return value


def _require_opaque(value: object) -> str:
    if type(value) is not str or _OPAQUE_ID.fullmatch(value) is None:
        raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
    return value


def _require_path(value: object) -> str:
    if type(value) is not str:
        raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED) from None
    if (
        not value
        or len(encoded) > _MAX_PATH_BYTES
        or any(character in value for character in "\\\x00\r\n")
    ):
        raise _error(SessionCheckpointFailureCode.PATH_NOT_ALLOWED)
    return value


def _optional_digest(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _error(SessionCheckpointFailureCode.SNAPSHOT_INVALID)
    return value


def _require_object_id(value: object) -> str:
    if type(value) is not str or _OBJECT_ID.fullmatch(value) is None:
        raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
    return value


def _optional_object_id(value: object) -> str | None:
    return None if value is None else _require_object_id(value)


def _optional_operation_id(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _OPERATION_ID.fullmatch(value) is None:
        raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
    return value


def _error(code: SessionCheckpointFailureCode) -> SessionCheckpointError:
    action = {
        SessionCheckpointFailureCode.SNAPSHOT_DRIFT: SessionCheckpointRecoveryAction.RETRY,
        SessionCheckpointFailureCode.SESSION_NOT_READY: SessionCheckpointRecoveryAction.RETRY,
        SessionCheckpointFailureCode.OUTCOME_UNKNOWN: SessionCheckpointRecoveryAction.INSPECT,
        SessionCheckpointFailureCode.RECOVERY_REQUIRED: SessionCheckpointRecoveryAction.OPERATOR_RECOVERY,
    }.get(code, SessionCheckpointRecoveryAction.NONE)
    error = SessionCheckpointError(code, action)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
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
]
