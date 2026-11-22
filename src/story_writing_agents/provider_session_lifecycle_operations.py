"""Compose Backend operations for provider session access and lifecycle."""

from __future__ import annotations

import story_writing_agents.provider_session_lifecycle_queries as provider_session_lifecycle_queries
from .debug_log import DebugOperation, DebugOutcome, DebugPhase
from .errors import (
    BackendProtocolError,
    SessionAccessBlockedError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    SessionPausedError,
)
from .session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)


def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
    """Return validated durable and access facts without mutation.

    Absence is represented only when no manifest or access claim exists.
    Old lifecycle artifacts raise ``WorkspaceVersionUnsupportedError`` and
    an access claim without a manifest raises ``WorkspaceFormatError``.
    """

    self.session_lifecycle_ensure_open()
    session = self.workspace.validate_identifier(session_id)
    try:
        state = provider_session_lifecycle_queries.get_session_lifecycle(
            self.workspace, session, self.session_lifecycle_access_transactions.access_state,
        )
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
            session_id=session,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.OBSERVED,
        session_id=session,
        outcome=(
            DebugOutcome.HELD_HERE
            if state.access_state is SessionAccessState.HELD_HERE
            else DebugOutcome.CLAIMABLE
            if state.access_state is SessionAccessState.CLAIMABLE
            else DebugOutcome.COMPLETED
        ),
    )
    return state


def list_session_lifecycles(self) -> tuple[SessionLifecycleState, ...]:
    """List validated persisted lifecycle facts without mutation.

    Results are ordered by newest durable update and then session ID.
    Unsupported version-1 artifacts and orphan access claims fail closed.
    """

    self.session_lifecycle_ensure_open()
    try:
        result = provider_session_lifecycle_queries.list_session_lifecycles(
            self.workspace, self.session_lifecycle_access_transactions.access_state,
        )
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.OBSERVED,
        outcome=DebugOutcome.COMPLETED,
    )
    return result


def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
    """Acquire or prove access without changing durable session bytes."""

    self.session_lifecycle_ensure_open()
    session = self.workspace.validate_identifier(session_id)
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.STARTED,
        session_id=session,
    )
    try:
        state, _ = self.session_lifecycle_access_transactions.acquire_access_with_reuse(
            self.workspace, session, self.get_session_lifecycle,
        )
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
            session_id=session,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.SUCCEEDED,
        session_id=session,
        outcome=DebugOutcome.HELD_HERE,
    )
    return state


def release_session_access(self, session_id: str) -> SessionLifecycleState:
    """Release only this backend's claim and return current lifecycle facts.

    Without a locally recorded claim the operation is a read-only no-op;
    foreign claims are never removed or rewritten.
    """

    self.session_lifecycle_ensure_open()
    session = self.workspace.validate_identifier(session_id)
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.STARTED,
        session_id=session,
    )
    try:
        self.session_lifecycle_access_transactions.release_owned_access_claim(
            self.workspace, session
        )
        state = self.get_session_lifecycle(session)
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
            session_id=session,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.SUCCEEDED,
        session_id=session,
        outcome=DebugOutcome.CLAIMABLE,
    )
    return state


def create_session_with_access(
    self,
    session_id: str,
) -> SessionLifecycleState:
    """Atomically create one ready V2 session and retain its access claim.

    Creation writes an empty provider-thread map with equal timestamps.
    Configuration validation remains the caller's separate operation.
    """

    self.session_lifecycle_ensure_open()
    session = self.workspace.validate_identifier(session_id)
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.STARTED,
        session_id=session,
    )
    try:
        state = self.session_lifecycle_transitions.create_session_with_access(
            self.workspace, session, self.get_session_lifecycle,
        )
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
            session_id=session,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.SUCCEEDED,
        session_id=session,
        outcome=DebugOutcome.READY,
    )
    return state


def reopen_session_with_access(
    self,
    session_id: str,
) -> SessionLifecycleState:
    """Acquire an existing session, make it ready, and retain access.

    An already-ready manifest is a byte- and timestamp-preserving no-op.
    A newly acquired claim is released if durable validation or writing
    fails; access already owned by this backend is retained on failure.
    """

    self.session_lifecycle_ensure_open()
    session = self.workspace.validate_identifier(session_id)
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.STARTED,
        session_id=session,
    )
    try:
        state = self.session_lifecycle_transitions.reopen_session_with_access(
            self.workspace, session, self.get_session_lifecycle,
        )
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
            session_id=session,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.SUCCEEDED,
        session_id=session,
        outcome=DebugOutcome.READY,
    )
    return state


def pause_session_and_release_access(
    self,
    session_id: str,
) -> SessionLifecycleState:
    """Acquire a session, make it paused, then release local access.

    An already-paused manifest is not rewritten. A durable-write failure
    releases only an access claim acquired by this call; after a successful
    pause, release is always attempted and its failure propagates.
    """

    self.session_lifecycle_ensure_open()
    session = self.workspace.validate_identifier(session_id)
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.STARTED,
        session_id=session,
    )
    try:
        state = self.session_lifecycle_transitions.pause_session_and_release_access(
            self.workspace, session, self.get_session_lifecycle,
        )
    except BaseException as exc:
        self.session_lifecycle_record_debug_error(
            DebugOperation.SESSION_LIFECYCLE,
            exc,
            session_id=session,
        )
        raise
    self.session_lifecycle_trace_debug(
        DebugOperation.SESSION_LIFECYCLE,
        DebugPhase.SUCCEEDED,
        session_id=session,
        outcome=DebugOutcome.PAUSED,
    )
    return state


def assert_provider_work_ready(self, session_id: str) -> None:
    """Require ready durable state and access already held by this backend."""

    state = self.get_session_lifecycle(session_id)
    if state.durable_state is SessionDurableState.ABSENT:
        raise SessionNotFoundError(
            f"provider session does not exist: {session_id}; "
            f"run 'story session create {session_id}'"
        )
    if state.durable_state is SessionDurableState.PAUSED:
        raise SessionPausedError(
            f"provider session is paused: {session_id}; "
            f"run 'story session reopen {session_id}'"
        )
    if state.access_state is SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER:
        raise SessionAccessBlockedError(
            f"provider session access is blocked: {session_id}"
        )
    if state.access_state is SessionAccessState.OWNERSHIP_UNKNOWN:
        raise SessionOwnershipUnknownError(
            f"provider session access ownership is unknown: {session_id}"
        )
    if not provider_work_is_ready(
        state,
        provider_configuration_ready=True,
    ):
        raise BackendProtocolError(
            f"provider session access is not held here: {session_id}"
        )
