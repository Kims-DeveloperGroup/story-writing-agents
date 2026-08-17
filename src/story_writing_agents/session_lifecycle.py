"""Pure provider-session lifecycle contracts and readiness derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


__all__ = [
    "SessionAccessState",
    "SessionDurableState",
    "SessionLifecycleState",
    "provider_work_is_ready",
]


_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class SessionDurableState(StrEnum):
    """Durable existence and provider-work state of a story session."""

    ABSENT = "absent"
    READY = "ready"
    PAUSED = "paused"


class SessionAccessState(StrEnum):
    """Observed claimability or ownership of story-session access."""

    CLAIMABLE = "claimable"
    HELD_HERE = "held_here"
    BLOCKED_BY_LIVE_LOCAL_OWNER = "blocked_by_live_local_owner"
    OWNERSHIP_UNKNOWN = "ownership_unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionLifecycleState:
    """Validated durable and access facts for one provider-backed session.

    Construction is pure. Wrong scalar or enum types raise ``TypeError``;
    malformed values and impossible fact combinations raise ``ValueError``.
    """

    session_id: str
    durable_state: SessionDurableState
    access_state: SessionAccessState
    owner_hostname: str | None
    owner_pid: int | None
    created_at: str | None
    updated_at: str | None

    def __post_init__(self) -> None:
        """Validate the complete immutable fact set without performing I/O."""

        _validate_lifecycle_state(self)


def provider_work_is_ready(
    state: SessionLifecycleState,
    *,
    provider_configuration_ready: bool,
) -> bool:
    """Return whether already-derived facts permit provider work.

    The query performs no configuration read, access acquisition, provider
    call, or persistence mutation. Inputs must use the exact public contract
    types; invalid inputs raise ``TypeError`` or ``ValueError``.
    """

    _validate_lifecycle_state(state)
    _validate_exact_bool(
        provider_configuration_ready,
        label="provider_configuration_ready",
    )
    return (
        provider_configuration_ready
        and state.durable_state is SessionDurableState.READY
        and state.access_state is SessionAccessState.HELD_HERE
    )


def _validate_lifecycle_state(state: SessionLifecycleState) -> None:
    """Validate one exact lifecycle value without external side effects."""

    if type(state) is not SessionLifecycleState:
        raise TypeError("state must be an exact SessionLifecycleState")
    if type(state.session_id) is not str:
        raise TypeError("session_id must be a string")
    if _SAFE_SESSION_ID.fullmatch(state.session_id) is None:
        raise ValueError(
            "session_id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
        )
    if type(state.durable_state) is not SessionDurableState:
        raise TypeError("durable_state must be a SessionDurableState")
    if type(state.access_state) is not SessionAccessState:
        raise TypeError("access_state must be a SessionAccessState")

    _validate_optional_hostname(state.owner_hostname)
    _validate_optional_pid(state.owner_pid)
    owner_is_present = state.owner_hostname is not None
    if owner_is_present != (state.owner_pid is not None):
        raise ValueError("owner_hostname and owner_pid must be present together")
    if state.access_state is not SessionAccessState.CLAIMABLE and not owner_is_present:
        raise ValueError("non-claimable access requires owner host and PID")

    _validate_optional_timestamp_type(state.created_at, label="created_at")
    _validate_optional_timestamp_type(state.updated_at, label="updated_at")
    timestamps_are_present = state.created_at is not None
    if timestamps_are_present != (state.updated_at is not None):
        raise ValueError("created_at and updated_at must be present together")

    if state.durable_state is SessionDurableState.ABSENT:
        if state.access_state is not SessionAccessState.CLAIMABLE:
            raise ValueError("an absent session must have claimable access")
        if owner_is_present:
            raise ValueError("an absent session cannot carry owner metadata")
        if timestamps_are_present:
            raise ValueError("an absent session cannot carry persisted timestamps")
        return

    if not timestamps_are_present:
        raise ValueError("a persisted session requires created_at and updated_at")
    created_at = _parse_offset_timestamp(state.created_at, label="created_at")
    updated_at = _parse_offset_timestamp(state.updated_at, label="updated_at")
    if updated_at < created_at:
        raise ValueError("updated_at must not precede created_at")


def _validate_optional_hostname(value: str | None) -> None:
    """Validate an optional nonempty hostname without performing I/O."""

    if value is None:
        return
    if type(value) is not str:
        raise TypeError("owner_hostname must be a string or None")
    if not value:
        raise ValueError("owner_hostname must not be empty")


def _validate_optional_pid(value: int | None) -> None:
    """Validate an optional positive exact integer process identifier."""

    if value is None:
        return
    if type(value) is not int:
        raise TypeError("owner_pid must be an integer or None")
    if value <= 0:
        raise ValueError("owner_pid must be positive")


def _validate_optional_timestamp_type(value: str | None, *, label: str) -> None:
    """Validate an optional timestamp scalar before semantic parsing."""

    if value is not None and type(value) is not str:
        raise TypeError(f"{label} must be a string or None")


def _parse_offset_timestamp(value: str | None, *, label: str) -> datetime:
    """Parse one required offset-aware timestamp or raise ``ValueError``."""

    if value is None:
        raise ValueError(f"{label} is required")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed


def _validate_exact_bool(value: bool, *, label: str) -> None:
    """Require an exact boolean without coercion."""

    if type(value) is not bool:
        raise TypeError(f"{label} must be a boolean")
