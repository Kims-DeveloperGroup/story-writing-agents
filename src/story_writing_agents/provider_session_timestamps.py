"""Timestamps stored in provider session manifests and thread records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import WorkspaceFormatError


def parse_record_timestamp(value: Any, *, label: str) -> datetime:
    """Return an offset-aware timestamp or raise ``WorkspaceFormatError``.

    Parsing performs no normalization or mutation; the supplied diagnostic
    label identifies empty, malformed, or offset-free values.
    """

    if not isinstance(value, str) or not value:
        raise WorkspaceFormatError(f"{label} must be a timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
        offset = parsed.utcoffset()
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorkspaceFormatError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or offset is None:
        raise WorkspaceFormatError(f"{label} must include a UTC offset")
    return parsed


def advance_record_timestamp(previous: str, *, candidate: str | None = None) -> str:
    """Return a timestamp strictly later than the validated previous value.

    The candidate, or current UTC time when absent, is preserved if later;
    otherwise the prior instant advances by one microsecond in UTC. Invalid or
    unadvanceable timestamps raise ``WorkspaceFormatError``.
    """

    prior = parse_record_timestamp(previous, label="provider session updated_at")
    proposed_text = record_timestamp() if candidate is None else candidate
    proposed = parse_record_timestamp(
        proposed_text,
        label="provider session timestamp",
    )
    if proposed > prior:
        return proposed_text
    try:
        return (prior.astimezone(UTC) + timedelta(microseconds=1)).isoformat()
    except OverflowError as exc:
        raise WorkspaceFormatError(
            "provider session updated_at cannot be advanced"
        ) from exc


def record_timestamp() -> str:
    return datetime.now(UTC).isoformat()


__all__ = (
    "advance_record_timestamp",
    "parse_record_timestamp",
    "record_timestamp",
)
