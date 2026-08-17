"""Pure provider-session lifecycle contracts and legacy projections."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType


__all__ = [
    "SessionAccessState",
    "SessionDurableState",
    "SessionLeaseState",
    "SessionLifecycleState",
    "SessionSummary",
    "lifecycle_from_session_summary",
    "provider_work_is_ready",
    "session_summary_from_lifecycle",
]


_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class SessionDurableState(StrEnum):
    """Durable existence and provider-work readiness of a story session."""

    ABSENT = "absent"
    READY = "ready"
    PAUSED = "paused"


class SessionAccessState(StrEnum):
    """Observed claimability or ownership of a story-session provider lease."""

    CLAIMABLE = "claimable"
    HELD_HERE = "held_here"
    BLOCKED_BY_LIVE_LOCAL_OWNER = "blocked_by_live_local_owner"
    OWNERSHIP_UNKNOWN = "ownership_unknown"


class SessionLeaseState(StrEnum):
    """Observed ownership and liveness state for a provider-session lease."""

    AVAILABLE = "available"
    OWNED = "owned"
    IN_USE = "in_use"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Immutable provider-session lifecycle status."""

    session_id: str
    active: bool
    selected: bool
    lease_state: SessionLeaseState
    lease_hostname: str | None
    lease_pid: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionLifecycleState:
    """Validated durable and access facts for one provider-backed session.

    Construction accepts only the declared scalar and enum types, returns an
    immutable value, and performs no I/O. Wrong types raise ``TypeError``;
    malformed values or impossible fact combinations raise ``ValueError``.
    """

    session_id: str
    durable_state: SessionDurableState
    access_state: SessionAccessState
    access_hostname: str | None
    access_pid: int | None
    created_at: str | None
    updated_at: str | None

    def __post_init__(self) -> None:
        """Return after validation without I/O or access acquisition.

        Wrong field types raise ``TypeError``; invalid values or combinations
        raise ``ValueError``.
        """

        _validate_lifecycle_state(self)


_ACTIVE_TO_DURABLE: Mapping[bool, SessionDurableState] = MappingProxyType(
    {
        True: SessionDurableState.READY,
        False: SessionDurableState.PAUSED,
    }
)
_DURABLE_TO_ACTIVE: Mapping[SessionDurableState, bool] = MappingProxyType(
    {
        SessionDurableState.READY: True,
        SessionDurableState.PAUSED: False,
    }
)
_LEASE_TO_ACCESS: Mapping[SessionLeaseState, SessionAccessState] = MappingProxyType(
    {
        SessionLeaseState.AVAILABLE: SessionAccessState.CLAIMABLE,
        SessionLeaseState.OWNED: SessionAccessState.HELD_HERE,
        SessionLeaseState.IN_USE: SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER,
        SessionLeaseState.UNVERIFIED: SessionAccessState.OWNERSHIP_UNKNOWN,
    }
)
_ACCESS_TO_LEASE: Mapping[SessionAccessState, SessionLeaseState] = MappingProxyType(
    {
        SessionAccessState.CLAIMABLE: SessionLeaseState.AVAILABLE,
        SessionAccessState.HELD_HERE: SessionLeaseState.OWNED,
        SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER: SessionLeaseState.IN_USE,
        SessionAccessState.OWNERSHIP_UNKNOWN: SessionLeaseState.UNVERIFIED,
    }
)


def lifecycle_from_session_summary(summary: SessionSummary) -> SessionLifecycleState:
    """Project one exact legacy summary into canonical lifecycle facts.

    ``summary.selected`` is validated but omitted because saved-default
    selection is not a lifecycle fact. The projection performs no I/O and
    returns a validated immutable state. A wrong contract type or field type
    raises ``TypeError``; invalid legacy values raise ``ValueError``.
    """

    _validate_legacy_summary(summary)
    return SessionLifecycleState(
        session_id=summary.session_id,
        durable_state=_ACTIVE_TO_DURABLE[summary.active],
        access_state=_LEASE_TO_ACCESS[summary.lease_state],
        access_hostname=summary.lease_hostname,
        access_pid=summary.lease_pid,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
    )


def session_summary_from_lifecycle(
    state: SessionLifecycleState,
    *,
    selected: bool,
) -> SessionSummary:
    """Project canonical persisted-session facts into one legacy summary.

    ``selected`` supplies the caller-owned saved-default projection. The pure
    conversion returns the exact legacy contract, raises ``TypeError`` for
    wrong contract or field types, and raises ``ValueError`` for invalid state
    or ``absent``, which the legacy summary cannot represent.
    """

    _validate_lifecycle_state(state)
    _validate_exact_bool(selected, label="selected")
    if state.durable_state is SessionDurableState.ABSENT:
        raise ValueError("an absent session has no legacy SessionSummary projection")
    created_at = state.created_at
    updated_at = state.updated_at
    if created_at is None or updated_at is None:
        raise ValueError("a persisted session requires created_at and updated_at")
    return SessionSummary(
        session_id=state.session_id,
        active=_DURABLE_TO_ACTIVE[state.durable_state],
        selected=selected,
        lease_state=_ACCESS_TO_LEASE[state.access_state],
        lease_hostname=state.access_hostname,
        lease_pid=state.access_pid,
        created_at=created_at,
        updated_at=updated_at,
    )


def provider_work_is_ready(
    state: SessionLifecycleState,
    *,
    provider_configuration_ready: bool,
) -> bool:
    """Report whether provider work may start from already-derived facts.

    The input must be an exact valid lifecycle state and the configuration
    fact must be an exact ``bool``. This pure query never claims access or
    performs I/O. Wrong types raise ``TypeError`` and invalid state raises
    ``ValueError``; otherwise the result is true only for ready, locally held
    access with valid provider configuration.
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


def _validate_legacy_summary(summary: SessionSummary) -> None:
    """Return after exact legacy type checks without mutation or I/O.

    A wrong contract, boolean, or lease-enum type raises ``TypeError``.
    """

    if type(summary) is not SessionSummary:
        raise TypeError("summary must be an exact SessionSummary")
    _validate_exact_bool(summary.active, label="active")
    _validate_exact_bool(summary.selected, label="selected")
    if type(summary.lease_state) is not SessionLeaseState:
        raise TypeError("lease_state must be a SessionLeaseState")


def _validate_lifecycle_state(state: SessionLifecycleState) -> None:
    """Return after exact lifecycle validation without mutation or I/O.

    Wrong contract or field types raise ``TypeError``; malformed values and
    impossible combinations raise ``ValueError``.
    """

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

    _validate_optional_hostname(state.access_hostname)
    _validate_optional_pid(state.access_pid)
    owner_is_present = state.access_hostname is not None
    if owner_is_present != (state.access_pid is not None):
        raise ValueError("access_hostname and access_pid must be present together")
    if (
        state.access_state is not SessionAccessState.CLAIMABLE
        and not owner_is_present
    ):
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
            raise ValueError("an absent session cannot carry lease ownership metadata")
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
    """Return after validating an optional hostname without I/O.

    A wrong type raises ``TypeError`` and empty text raises ``ValueError``.
    """

    if value is None:
        return
    if type(value) is not str:
        raise TypeError("access_hostname must be a string or None")
    if not value:
        raise ValueError("access_hostname must not be empty")


def _validate_optional_pid(value: int | None) -> None:
    """Return after validating an optional positive PID without I/O.

    A wrong type raises ``TypeError`` and a nonpositive value raises
    ``ValueError``.
    """

    if value is None:
        return
    if type(value) is not int:
        raise TypeError("access_pid must be an integer or None")
    if value <= 0:
        raise ValueError("access_pid must be positive")


def _validate_optional_timestamp_type(value: str | None, *, label: str) -> None:
    """Return after an optional timestamp type check without parsing or I/O.

    A non-string, non-``None`` input raises ``TypeError``.
    """

    if value is not None and type(value) is not str:
        raise TypeError(f"{label} must be a string or None")


def _parse_offset_timestamp(value: str | None, *, label: str) -> datetime:
    """Return one parsed offset-aware timestamp without external effects.

    Missing, blank, malformed, or offset-naive input raises ``ValueError``.
    """

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
    """Return for an exact boolean without coercion or side effects.

    Any non-``bool`` input raises ``TypeError``.
    """

    if type(value) is not bool:
        raise TypeError(f"{label} must be a boolean")
